"""
Price Engine — Multi-DEX Price Fetching & Opportunity Detection
================================================================
Queries Uniswap V3 (via Quoter), SushiSwap V2, Camelot, and Aerodrome
to find cross-DEX arbitrage opportunities for all configured pairs.
"""

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from web3 import Web3

from config.chains import ChainConfig, DexConfig

logger = logging.getLogger(__name__)

# ─── ABIs ────────────────────────────────────────────────────────────────────

UNISWAP_V3_QUOTER_ABI = json.loads("""[
  {
    "name": "quoteExactInputSingle",
    "type": "function",
    "inputs": [
      {"name": "tokenIn", "type": "address"},
      {"name": "tokenOut", "type": "address"},
      {"name": "fee", "type": "uint24"},
      {"name": "amountIn", "type": "uint256"},
      {"name": "sqrtPriceLimitX96", "type": "uint160"}
    ],
    "outputs": [{"name": "amountOut", "type": "uint256"}],
    "stateMutability": "view"
  }
]""")

V2_ROUTER_ABI = json.loads("""[
  {
    "name": "getAmountsOut",
    "type": "function",
    "inputs": [
      {"name": "amountIn", "type": "uint256"},
      {"name": "path", "type": "address[]"}
    ],
    "outputs": [{"name": "amounts", "type": "uint256[]"}],
    "stateMutability": "view"
  }
]""")

V3_FEE_TIERS = [100, 500, 3000, 10000]


# ─── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class PriceQuote:
    dex_name: str
    dex_key: str
    token_in: str
    token_out: str
    amount_in: int
    amount_out: int
    fee_tier: Optional[int] = None  # V3 only

    @property
    def rate(self) -> float:
        """Human-readable output/input ratio."""
        return self.amount_out / self.amount_in if self.amount_in > 0 else 0


@dataclass
class ArbOpportunity:
    chain: str
    token_a: str
    token_b: str
    buy_dex: str
    sell_dex: str
    buy_quote: PriceQuote
    sell_quote: PriceQuote
    gross_profit: int       # In base token smallest units
    gross_profit_pct: float
    estimated_gas_cost: int  # In base token smallest units
    net_profit: int
    flash_loan_premium: int  # Aave fee (usually 0.05%)


# ─── Price Engine ────────────────────────────────────────────────────────────

class PriceEngine:
    def __init__(self, w3: Web3, chain_config: ChainConfig):
        self.w3 = w3
        self.config = chain_config
        self._init_contracts()

    def _init_contracts(self):
        """Initialize contract instances for all DEXes."""
        self.v3_quoters: Dict[str, any] = {}
        self.v2_routers: Dict[str, any] = {}

        for dex_key, dex in self.config.dexes.items():
            addr = Web3.to_checksum_address(dex.router)
            if dex.dex_type == "v3" and dex.quoter:
                quoter_addr = Web3.to_checksum_address(dex.quoter)
                self.v3_quoters[dex_key] = self.w3.eth.contract(
                    address=quoter_addr, abi=UNISWAP_V3_QUOTER_ABI
                )
            elif dex.dex_type == "v2":
                self.v2_routers[dex_key] = self.w3.eth.contract(
                    address=addr, abi=V2_ROUTER_ABI
                )

    # ─── Quote Methods ───────────────────────────────────────────────────

    def get_v3_quote(
        self, dex_key: str, token_in: str, token_out: str, amount_in: int
    ) -> Optional[PriceQuote]:
        """Get best quote across all V3 fee tiers."""
        quoter = self.v3_quoters.get(dex_key)
        if quoter is None:
            return None

        dex = self.config.dexes[dex_key]
        addr_in = Web3.to_checksum_address(self.config.tokens[token_in])
        addr_out = Web3.to_checksum_address(self.config.tokens[token_out])

        best_out = 0
        best_fee = None

        for fee in V3_FEE_TIERS:
            try:
                out = quoter.functions.quoteExactInputSingle(
                    addr_in, addr_out, fee, amount_in, 0
                ).call()
                if out > best_out:
                    best_out = out
                    best_fee = fee
            except Exception:
                continue

        if best_out == 0:
            return None

        return PriceQuote(
            dex_name=dex.name,
            dex_key=dex_key,
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            amount_out=best_out,
            fee_tier=best_fee,
        )

    def get_v2_quote(
        self, dex_key: str, token_in: str, token_out: str, amount_in: int
    ) -> Optional[PriceQuote]:
        """Get quote from a V2-style DEX."""
        router = self.v2_routers.get(dex_key)
        if router is None:
            return None

        dex = self.config.dexes[dex_key]
        addr_in = Web3.to_checksum_address(self.config.tokens[token_in])
        addr_out = Web3.to_checksum_address(self.config.tokens[token_out])

        try:
            amounts = router.functions.getAmountsOut(
                amount_in, [addr_in, addr_out]
            ).call()
            amount_out = amounts[-1]
            if amount_out == 0:
                return None

            return PriceQuote(
                dex_name=dex.name,
                dex_key=dex_key,
                token_in=token_in,
                token_out=token_out,
                amount_in=amount_in,
                amount_out=amount_out,
            )
        except Exception as e:
            logger.debug(f"V2 quote failed {dex_key} {token_in}->{token_out}: {e}")
            return None

    def get_quote(
        self, dex_key: str, token_in: str, token_out: str, amount_in: int
    ) -> Optional[PriceQuote]:
        """Get quote from any DEX by key."""
        dex = self.config.dexes.get(dex_key)
        if dex is None:
            return None
        if dex.dex_type == "v3":
            return self.get_v3_quote(dex_key, token_in, token_out, amount_in)
        else:
            return self.get_v2_quote(dex_key, token_in, token_out, amount_in)

    # ─── Opportunity Detection ───────────────────────────────────────────

    def get_all_quotes_for_pair(
        self, token_in: str, token_out: str, amount_in: int
    ) -> List[PriceQuote]:
        """Get quotes from all DEXes for a token pair."""
        quotes = []
        for dex_key in self.config.dexes:
            quote = self.get_quote(dex_key, token_in, token_out, amount_in)
            if quote is not None:
                quotes.append(quote)
        return quotes

    def find_opportunities(
        self,
        trade_sizes: Dict[str, int],
        min_profit_pct: float = 0.1,
        gas_price_gwei: Optional[float] = None,
    ) -> List[ArbOpportunity]:
        """
        Scan all pairs for cross-DEX arbitrage opportunities.

        For each pair (A, B), checks:
          - Buy B with A on DEX1, sell B for A on DEX2
          - Buy B with A on DEX2, sell B for A on DEX1
          (and vice-versa for reverse direction)

        Args:
            trade_sizes: Dict mapping token symbol to trade amount (in smallest units)
            min_profit_pct: Minimum profit percentage to report
            gas_price_gwei: Current gas price (auto-fetched if None)
        """
        if gas_price_gwei is None:
            try:
                gas_price_gwei = self.w3.from_wei(self.w3.eth.gas_price, "gwei")
            except Exception:
                gas_price_gwei = 0.1  # Arbitrum/Base default

        opportunities = []
        dex_keys = list(self.config.dexes.keys())

        for token_a, token_b in self.config.pairs:
            amount_in = trade_sizes.get(token_a, 0)
            if amount_in == 0:
                continue

            # Get all forward quotes: A -> B
            forward_quotes = self.get_all_quotes_for_pair(token_a, token_b, amount_in)
            if len(forward_quotes) < 2:
                continue

            # For each forward quote, get reverse quotes: B -> A
            for buy_q in forward_quotes:
                b_amount = buy_q.amount_out
                reverse_quotes = self.get_all_quotes_for_pair(token_b, token_a, b_amount)

                for sell_q in reverse_quotes:
                    # Skip same DEX
                    if sell_q.dex_key == buy_q.dex_key:
                        continue

                    gross_profit = sell_q.amount_out - amount_in
                    if gross_profit <= 0:
                        continue

                    profit_pct = (gross_profit / amount_in) * 100

                    # Estimate gas cost in token A terms
                    estimated_gas = self._estimate_gas_cost(
                        token_a, gas_price_gwei
                    )

                    # Aave flash loan premium: 0.05%
                    flash_premium = int(amount_in * 0.0005)

                    net_profit = gross_profit - estimated_gas - flash_premium

                    if profit_pct >= min_profit_pct and net_profit > 0:
                        opp = ArbOpportunity(
                            chain=self.config.name,
                            token_a=token_a,
                            token_b=token_b,
                            buy_dex=buy_q.dex_name,
                            sell_dex=sell_q.dex_name,
                            buy_quote=buy_q,
                            sell_quote=sell_q,
                            gross_profit=gross_profit,
                            gross_profit_pct=profit_pct,
                            estimated_gas_cost=estimated_gas,
                            net_profit=net_profit,
                            flash_loan_premium=flash_premium,
                        )
                        opportunities.append(opp)
                        logger.info(
                            f"💰 [{self.config.name}] {token_a}->{token_b}: "
                            f"Buy on {buy_q.dex_name}, Sell on {sell_q.dex_name} | "
                            f"Gross: {profit_pct:.3f}% | "
                            f"Net: {net_profit} ({token_a} units)"
                        )

        # Sort by net profit descending
        opportunities.sort(key=lambda x: x.net_profit, reverse=True)
        return opportunities

    def _estimate_gas_cost(self, base_token: str, gas_price_gwei: float) -> int:
        """
        Estimate gas cost for a 2-swap flash loan arb in base_token units.
        Flash loan + 2 swaps ≈ 350,000 gas on L2.
        """
        gas_units = 350_000
        gas_cost_eth = gas_units * gas_price_gwei * 1e-9  # In ETH

        if base_token == "WETH":
            return int(gas_cost_eth * 1e18)

        # Convert ETH gas cost to base_token via a quick quote
        try:
            eth_amount = int(gas_cost_eth * 1e18)
            if eth_amount == 0:
                return 0
            quote = self.get_quote(
                "uniswap_v3", "WETH", base_token, eth_amount
            )
            return quote.amount_out if quote else 0
        except Exception:
            return 0

    # ─── WETH Price Helper ───────────────────────────────────────────────

    def get_weth_price_in(self, token: str) -> float:
        """Get price of 1 WETH in terms of another token."""
        one_eth = 10**18
        quote = self.get_quote("uniswap_v3", "WETH", token, one_eth)
        if quote is None:
            return 0
        decimals = self.config.decimals.get(token, 18)
        return quote.amount_out / (10**decimals)
