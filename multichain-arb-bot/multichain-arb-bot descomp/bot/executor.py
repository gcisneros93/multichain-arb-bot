"""
Transaction Executor — Builds & Sends Flash Loan Arbitrage TXs
================================================================
Encodes swap steps, calls the FlashArbExecutor contract, handles
gas estimation and nonce management.
"""

import json
import logging
import time
from typing import Optional

from web3 import Web3

from config.chains import ChainConfig, ROUTER_IDS
from bot.price_engine import ArbOpportunity

logger = logging.getLogger(__name__)

# ─── Contract ABI (FlashArbExecutor) ────────────────────────────────────────

FLASH_ARB_ABI = json.loads("""[
  {
    "inputs": [
      {
        "components": [
          {"name": "flashToken", "type": "address"},
          {"name": "flashAmount", "type": "uint256"},
          {
            "components": [
              {"name": "routerId", "type": "uint8"},
              {"name": "dexType", "type": "uint8"},
              {"name": "tokenIn", "type": "address"},
              {"name": "tokenOut", "type": "address"},
              {"name": "fee", "type": "uint24"},
              {"name": "v2Path", "type": "address[]"}
            ],
            "name": "steps",
            "type": "tuple[]"
          },
          {"name": "minProfit", "type": "uint256"}
        ],
        "name": "params",
        "type": "tuple"
      }
    ],
    "name": "executeFlashArbitrage",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
  },
  {
    "inputs": [
      {
        "components": [
          {"name": "routerId", "type": "uint8"},
          {"name": "dexType", "type": "uint8"},
          {"name": "tokenIn", "type": "address"},
          {"name": "tokenOut", "type": "address"},
          {"name": "fee", "type": "uint24"},
          {"name": "v2Path", "type": "address[]"}
        ],
        "name": "steps",
        "type": "tuple[]"
      },
      {"name": "baseToken", "type": "address"},
      {"name": "amountIn", "type": "uint256"},
      {"name": "minProfit", "type": "uint256"}
    ],
    "name": "executeDirectArbitrage",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
  },
  {
    "inputs": [
      {"name": "token", "type": "address"},
      {"name": "amount", "type": "uint256"}
    ],
    "name": "rescueTokens",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
  },
  {
    "inputs": [],
    "name": "rescueETH",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
  }
]""")


class TxExecutor:
    def __init__(
        self,
        w3: Web3,
        chain_config: ChainConfig,
        chain_key: str,
        contract_address: str,
        private_key: str,
        wallet_address: str,
        gas_multiplier: float = 1.3,
        slippage_tolerance: float = 0.98,
    ):
        self.w3 = w3
        self.config = chain_config
        self.chain_key = chain_key
        self.contract_address = Web3.to_checksum_address(contract_address)
        self.private_key = private_key
        self.wallet_address = Web3.to_checksum_address(wallet_address)
        self.gas_multiplier = gas_multiplier
        self.slippage_tolerance = slippage_tolerance

        self.contract = w3.eth.contract(
            address=self.contract_address, abi=FLASH_ARB_ABI
        )
        self.router_ids = ROUTER_IDS.get(chain_key, {})

    # ─── Nonce Management ────────────────────────────────────────────────

    def _get_nonce(self) -> int:
        time.sleep(0.3)
        return self.w3.eth.get_transaction_count(self.wallet_address, "pending")

    # ─── Build Swap Steps ────────────────────────────────────────────────

    def _build_swap_step(self, opp: ArbOpportunity, is_buy: bool) -> tuple:
        """
        Build a SwapStep tuple for the smart contract.
        Returns: (routerId, dexType, tokenIn, tokenOut, fee, v2Path)
        """
        if is_buy:
            quote = opp.buy_quote
            token_in = opp.token_a
            token_out = opp.token_b
        else:
            quote = opp.sell_quote
            token_in = opp.token_b
            token_out = opp.token_a

        router_id = self.router_ids.get(quote.dex_key, 0)
        dex = self.config.dexes.get(quote.dex_key)

        if dex and dex.dex_type == "v3":
            dex_type = 0  # UniswapV3
            fee = quote.fee_tier or 3000
            v2_path = []
        else:
            dex_type = 1  # UniswapV2
            fee = 0
            v2_path = []  # Simple 2-token path is default in the contract

        addr_in = Web3.to_checksum_address(self.config.tokens[token_in])
        addr_out = Web3.to_checksum_address(self.config.tokens[token_out])

        return (router_id, dex_type, addr_in, addr_out, fee, v2_path)

    # ─── Flash Loan Execution ────────────────────────────────────────────

    def execute_flash_arb(
        self, opp: ArbOpportunity, min_profit_override: Optional[int] = None
    ) -> Optional[str]:
        """
        Execute flash loan arbitrage for a detected opportunity.
        """
        try:
            flash_token = Web3.to_checksum_address(
                self.config.tokens[opp.token_a]
            )
            flash_amount = opp.buy_quote.amount_in

            step_buy = self._build_swap_step(opp, is_buy=True)
            step_sell = self._build_swap_step(opp, is_buy=False)
            steps = [step_buy, step_sell]

            min_profit = min_profit_override if min_profit_override is not None else max(1, opp.net_profit // 2)

            params = (flash_token, flash_amount, steps, min_profit)

            nonce = self._get_nonce()
            base_fee = self.w3.eth.gas_price
            max_priority_fee = self.w3.to_wei(0.01, "gwei")  # L2 low priority
            max_fee = int(base_fee * self.gas_multiplier) + max_priority_fee

            txn = self.contract.functions.executeFlashArbitrage(
                params
            ).build_transaction({
                "from": self.wallet_address,
                "gas": 500_000,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": max_priority_fee,
                "nonce": nonce,
                "chainId": self.config.chain_id,
            })

            # Try to estimate gas
            try:
                estimated_gas = self.w3.eth.estimate_gas(txn)
                txn["gas"] = int(estimated_gas * 1.5)
            except Exception as e:
                logger.warning(f"Gas estimation failed, using default: {e}")

            signed = self.w3.eth.account.sign_transaction(txn, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            hash_hex = tx_hash.hex()

            logger.info(
                f"✅ [{self.config.name}] Flash arb TX sent: {hash_hex} | "
                f"{opp.token_a}->{opp.token_b} | "
                f"Buy: {opp.buy_dex} Sell: {opp.sell_dex}"
            )
            return hash_hex

        except Exception as e:
            logger.error(
                f"❌ [{self.config.name}] Flash arb TX failed: {e} | "
                f"{opp.token_a}->{opp.token_b}"
            )
            return None

    # ─── Direct Arb (No Flash Loan) ──────────────────────────────────────

    def execute_direct_arb(
        self, opp: ArbOpportunity, min_profit: int = 0
    ) -> Optional[str]:
        """Execute arbitrage using contract's existing balance (no flash loan)."""
        try:
            base_token = Web3.to_checksum_address(
                self.config.tokens[opp.token_a]
            )
            amount_in = opp.buy_quote.amount_in

            step_buy = self._build_swap_step(opp, is_buy=True)
            step_sell = self._build_swap_step(opp, is_buy=False)
            steps = [step_buy, step_sell]

            nonce = self._get_nonce()
            base_fee = self.w3.eth.gas_price
            max_priority_fee = self.w3.to_wei(0.01, "gwei")
            max_fee = int(base_fee * self.gas_multiplier) + max_priority_fee

            txn = self.contract.functions.executeDirectArbitrage(
                steps, base_token, amount_in, min_profit
            ).build_transaction({
                "from": self.wallet_address,
                "gas": 400_000,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": max_priority_fee,
                "nonce": nonce,
                "chainId": self.config.chain_id,
            })

            try:
                estimated_gas = self.w3.eth.estimate_gas(txn)
                txn["gas"] = int(estimated_gas * 1.5)
            except Exception:
                pass

            signed = self.w3.eth.account.sign_transaction(txn, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            hash_hex = tx_hash.hex()

            logger.info(
                f"✅ [{self.config.name}] Direct arb TX sent: {hash_hex}"
            )
            return hash_hex

        except Exception as e:
            logger.error(f"❌ [{self.config.name}] Direct arb TX failed: {e}")
            return None

    # ─── Rescue ──────────────────────────────────────────────────────────

    def rescue_tokens(self, token_symbol: str, amount: int) -> Optional[str]:
        """Rescue tokens stuck in the contract."""
        try:
            token_addr = Web3.to_checksum_address(
                self.config.tokens[token_symbol]
            )
            nonce = self._get_nonce()
            txn = self.contract.functions.rescueTokens(
                token_addr, amount
            ).build_transaction({
                "from": self.wallet_address,
                "gas": 100_000,
                "gasPrice": int(self.w3.eth.gas_price * self.gas_multiplier),
                "nonce": nonce,
                "chainId": self.config.chain_id,
            })
            signed = self.w3.eth.account.sign_transaction(txn, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info(f"✅ Rescue TX sent: {tx_hash.hex()}")
            return tx_hash.hex()
        except Exception as e:
            logger.error(f"❌ Rescue failed: {e}")
            return None
