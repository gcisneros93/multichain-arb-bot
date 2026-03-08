// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

// ============================================================================
// FlashArbExecutor — Multi-Pair Flash Loan Arbitrage on Arbitrum & Base
// ============================================================================
// Supports:
//   - Aave V3 flash loans (available on both Arbitrum & Base)
//   - Uniswap V3 + SushiSwap V2 + Camelot (Arbitrum) / Aerodrome (Base)
//   - Arbitrary token pair routing
//   - Multi-hop swap paths
// ============================================================================

// ─── Interfaces ─────────────────────────────────────────────────────────────

interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address recipient, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

interface IPoolAddressesProvider {
    function getPool() external view returns (address);
}

interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IUniswapV3Router {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params)
        external payable returns (uint256 amountOut);

    struct ExactInputParams {
        bytes path;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }
    function exactInput(ExactInputParams calldata params)
        external payable returns (uint256 amountOut);
}

interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint amountIn,
        uint amountOutMin,
        address[] calldata path,
        address to,
        uint deadline
    ) external returns (uint[] memory amounts);

    function getAmountsOut(uint amountIn, address[] calldata path)
        external view returns (uint[] memory amounts);
}

// ─── Contract ───────────────────────────────────────────────────────────────

contract FlashArbExecutor {
    address public immutable owner;
    IPoolAddressesProvider public immutable aaveProvider;

    // DEX router registry: routerId => router address
    mapping(uint8 => address) public routers;

    // Reentrancy guard
    uint256 private constant _NOT_ENTERED = 1;
    uint256 private constant _ENTERED = 2;
    uint256 private _status;

    // ─── Enums ──────────────────────────────────────────────────────────

    // DEX types for routing
    enum DexType { UniswapV3, UniswapV2 }

    // ─── Structs ────────────────────────────────────────────────────────

    // Describes a single swap leg
    struct SwapStep {
        uint8 routerId;         // Index into routers mapping
        DexType dexType;        // V2 or V3
        address tokenIn;
        address tokenOut;
        uint24 fee;             // Only used for V3 (ignored for V2)
        address[] v2Path;       // Only used for V2 multi-hop (empty for V3)
    }

    // Full arbitrage parameters passed through flash loan
    struct ArbParams {
        address flashToken;     // Token borrowed via flash loan
        uint256 flashAmount;    // Amount borrowed
        SwapStep[] steps;       // Ordered swap steps
        uint256 minProfit;      // Minimum profit in flashToken units
    }

    // ─── Events ─────────────────────────────────────────────────────────

    event ArbitrageExecuted(
        address indexed flashToken,
        uint256 flashAmount,
        uint256 profit,
        uint256 timestamp
    );

    event RouterUpdated(uint8 indexed routerId, address router);

    // ─── Modifiers ──────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    modifier nonReentrant() {
        require(_status != _ENTERED, "Reentrant call");
        _status = _ENTERED;
        _;
        _status = _NOT_ENTERED;
    }

    // ─── Constructor ────────────────────────────────────────────────────

    constructor(address _aaveProvider, address[] memory _routers) {
        require(_aaveProvider != address(0), "Invalid Aave provider");
        owner = msg.sender;
        aaveProvider = IPoolAddressesProvider(_aaveProvider);
        _status = _NOT_ENTERED;

        for (uint8 i = 0; i < _routers.length; i++) {
            require(_routers[i] != address(0), "Invalid router");
            routers[i] = _routers[i];
            emit RouterUpdated(i, _routers[i]);
        }
    }

    // ─── Admin ──────────────────────────────────────────────────────────

    function setRouter(uint8 routerId, address router) external onlyOwner {
        require(router != address(0), "Invalid router");
        routers[routerId] = router;
        emit RouterUpdated(routerId, router);
    }

    function rescueTokens(address token, uint256 amount) external onlyOwner {
        require(token != address(0), "Invalid token");
        IERC20(token).transfer(owner, amount);
    }

    function rescueETH() external onlyOwner {
        payable(owner).transfer(address(this).balance);
    }

    receive() external payable {}

    // ─── Flash Loan Entry Point ─────────────────────────────────────────

    /// @notice Initiates a flash loan and executes the arbitrage
    /// @param params Encoded ArbParams struct
    function executeFlashArbitrage(ArbParams calldata params) external onlyOwner nonReentrant {
        require(params.steps.length >= 2, "Need >= 2 swap steps");
        require(params.flashAmount > 0, "Flash amount must be > 0");

        address pool = aaveProvider.getPool();
        bytes memory encodedParams = abi.encode(params);

        IPool(pool).flashLoanSimple(
            address(this),
            params.flashToken,
            params.flashAmount,
            encodedParams,
            0 // referralCode
        );
    }

    /// @notice Aave V3 flash loan callback
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        require(initiator == address(this), "Invalid initiator");
        require(msg.sender == aaveProvider.getPool(), "Invalid caller");

        ArbParams memory arbParams = abi.decode(params, (ArbParams));

        // Execute all swap steps sequentially
        uint256 currentAmount = amount;
        address currentToken = asset;

        for (uint256 i = 0; i < arbParams.steps.length; i++) {
            SwapStep memory step = arbParams.steps[i];
            require(step.tokenIn == currentToken, "Token mismatch in chain");

            currentAmount = _executeSwap(step, currentAmount);
            currentToken = step.tokenOut;
        }

        // After all swaps, we should have the flash token back
        require(currentToken == asset, "Must end with flash token");

        // Repay flash loan + premium
        uint256 totalDebt = amount + premium;
        require(currentAmount >= totalDebt + arbParams.minProfit, "Not profitable");

        IERC20(asset).approve(msg.sender, totalDebt);

        // Send profit to owner
        uint256 profit = currentAmount - totalDebt;
        if (profit > 0) {
            IERC20(asset).transfer(owner, profit);
        }

        emit ArbitrageExecuted(asset, amount, profit, block.timestamp);
        return true;
    }

    // ─── Non-Flash-Loan Arbitrage (uses contract balance) ───────────────

    /// @notice Execute arbitrage without flash loan, using contract's own balance
    function executeDirectArbitrage(
        SwapStep[] calldata steps,
        address baseToken,
        uint256 amountIn,
        uint256 minProfit
    ) external onlyOwner nonReentrant {
        require(steps.length >= 2, "Need >= 2 swap steps");

        uint256 initialBalance = IERC20(baseToken).balanceOf(address(this));
        require(initialBalance >= amountIn, "Insufficient balance");

        uint256 currentAmount = amountIn;
        address currentToken = baseToken;

        for (uint256 i = 0; i < steps.length; i++) {
            require(steps[i].tokenIn == currentToken, "Token mismatch");
            currentAmount = _executeSwap(steps[i], currentAmount);
            currentToken = steps[i].tokenOut;
        }

        require(currentToken == baseToken, "Must end with base token");

        uint256 finalBalance = IERC20(baseToken).balanceOf(address(this));
        uint256 profit = finalBalance - initialBalance;
        require(profit >= minProfit, "Below min profit");

        if (profit > 0) {
            IERC20(baseToken).transfer(owner, profit);
        }

        emit ArbitrageExecuted(baseToken, amountIn, profit, block.timestamp);
    }

    // ─── Internal Swap Logic ────────────────────────────────────────────

    function _executeSwap(SwapStep memory step, uint256 amountIn) internal returns (uint256) {
        address router = routers[step.routerId];
        require(router != address(0), "Router not configured");

        IERC20(step.tokenIn).approve(router, amountIn);

        if (step.dexType == DexType.UniswapV3) {
            return _swapV3(router, step, amountIn);
        } else {
            return _swapV2(router, step, amountIn);
        }
    }

    function _swapV3(
        address router,
        SwapStep memory step,
        uint256 amountIn
    ) internal returns (uint256) {
        IUniswapV3Router.ExactInputSingleParams memory params = IUniswapV3Router.ExactInputSingleParams({
            tokenIn: step.tokenIn,
            tokenOut: step.tokenOut,
            fee: step.fee,
            recipient: address(this),
            deadline: block.timestamp + 300,
            amountIn: amountIn,
            amountOutMinimum: 0,
            sqrtPriceLimitX96: 0
        });

        return IUniswapV3Router(router).exactInputSingle(params);
    }

    function _swapV2(
        address router,
        SwapStep memory step,
        uint256 amountIn
    ) internal returns (uint256) {
        address[] memory path;

        if (step.v2Path.length > 0) {
            path = step.v2Path;
        } else {
            path = new address[](2);
            path[0] = step.tokenIn;
            path[1] = step.tokenOut;
        }

        uint[] memory amounts = IUniswapV2Router(router).swapExactTokensForTokens(
            amountIn,
            0,
            path,
            address(this),
            block.timestamp + 300
        );

        return amounts[amounts.length - 1];
    }

    // ─── View Helpers ───────────────────────────────────────────────────

    /// @notice Simulate a V2 swap output
    function quoteV2(
        uint8 routerId,
        uint256 amountIn,
        address[] calldata path
    ) external view returns (uint256) {
        address router = routers[routerId];
        require(router != address(0), "Router not configured");
        uint[] memory amounts = IUniswapV2Router(router).getAmountsOut(amountIn, path);
        return amounts[amounts.length - 1];
    }
}
