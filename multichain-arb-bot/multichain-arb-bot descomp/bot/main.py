"""
Multi-Chain Multi-Pair Flash Loan Arbitrage Bot
=================================================
Scans Arbitrum and Base for cross-DEX arbitrage opportunities
across multiple token pairs. Uses Aave V3 flash loans for
capital-efficient execution.

Usage:
    python -m bot.main

Environment variables (see .env.example):
    PRIVATE_KEY, WALLET_ADDRESS
    ARBITRUM_RPC, BASE_RPC
    ARBITRUM_CONTRACT, BASE_CONTRACT
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from web3 import Web3

from config.chains import CHAINS, ChainConfig
from bot.price_engine import PriceEngine, ArbOpportunity
from bot.executor import TxExecutor

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("arbitrage.log"),
    ],
)
logger = logging.getLogger(__name__)

# Success-only log
class SuccessFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return "TX sent" in msg or "profitable" in msg.lower()

success_handler = logging.FileHandler("successful_trades.log")
success_handler.setLevel(logging.INFO)
success_handler.addFilter(SuccessFilter())
success_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(success_handler)

# ─── Configuration ───────────────────────────────────────────────────────────

load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")

# Per-chain contract addresses
CONTRACT_ADDRESSES = {
    "arbitrum": os.getenv("ARBITRUM_CONTRACT", ""),
    "base": os.getenv("BASE_CONTRACT", ""),
}

# Execution parameters
SLIPPAGE_TOLERANCE = float(os.getenv("SLIPPAGE_TOLERANCE", "0.98"))
GAS_MULTIPLIER = float(os.getenv("GAS_MULTIPLIER", "1.3"))
MIN_PROFIT_PCT = float(os.getenv("MIN_PROFIT_PCT", "0.1"))  # 0.1%
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "5"))  # seconds
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "50"))
USE_FLASH_LOANS = os.getenv("USE_FLASH_LOANS", "true").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Trade sizes per base token (in smallest units)
# Adjust these based on your capital / flash loan limits
TRADE_SIZES = {
    "WETH":   int(os.getenv("TRADE_SIZE_WETH",   str(10**17))),      # 0.1 ETH
    "USDC":   int(os.getenv("TRADE_SIZE_USDC",    str(100_000_000))), # 100 USDC
    "USDC.e": int(os.getenv("TRADE_SIZE_USDC_E",  str(100_000_000))),
    "USDbC":  int(os.getenv("TRADE_SIZE_USDC_BC", str(100_000_000))),
    "USDT":   int(os.getenv("TRADE_SIZE_USDT",    str(100_000_000))),
    "WBTC":   int(os.getenv("TRADE_SIZE_WBTC",    str(100_000))),     # 0.001 BTC
    "ARB":    int(os.getenv("TRADE_SIZE_ARB",      str(100 * 10**18))),
    "MAGIC":  int(os.getenv("TRADE_SIZE_MAGIC",    str(100 * 10**18))),
    "DAI":    int(os.getenv("TRADE_SIZE_DAI",      str(100 * 10**18))),
    "LINK":   int(os.getenv("TRADE_SIZE_LINK",     str(10 * 10**18))),
    "cbETH":  int(os.getenv("TRADE_SIZE_CBETH",    str(10**17))),
    "AERO":   int(os.getenv("TRADE_SIZE_AERO",     str(100 * 10**18))),
    "GMX":    int(os.getenv("TRADE_SIZE_GMX",      str(10**18))),
    "PENDLE": int(os.getenv("TRADE_SIZE_PENDLE",   str(50 * 10**18))),
    "BRETT":  int(os.getenv("TRADE_SIZE_BRETT",     str(1000 * 10**18))),
    "DEGEN":  int(os.getenv("TRADE_SIZE_DEGEN",    str(10000 * 10**18))),
    "TOSHI":  int(os.getenv("TRADE_SIZE_TOSHI",    str(100000 * 10**18))),
}

# Enabled chains
ENABLED_CHAINS = [c.strip() for c in os.getenv("ENABLED_CHAINS", "arbitrum,base").split(",")]


# ─── Chain Manager ───────────────────────────────────────────────────────────

class ChainInstance:
    """Manages a single chain's connection, price engine, and executor."""

    def __init__(self, chain_key: str, config: ChainConfig):
        self.chain_key = chain_key
        self.config = config
        self.w3: Optional[Web3] = None
        self.price_engine: Optional[PriceEngine] = None
        self.executor: Optional[TxExecutor] = None

    def connect(self) -> bool:
        rpc_url = os.getenv(self.config.rpc_env_key, "")
        if not rpc_url:
            logger.warning(f"⚠️ No RPC URL for {self.config.name} ({self.config.rpc_env_key})")
            return False

        try:
            if rpc_url.startswith("wss://"):
                self.w3 = Web3(Web3.WebsocketProvider(rpc_url))
            else:
                self.w3 = Web3(Web3.HTTPProvider(rpc_url))

            if not self.w3.is_connected():
                logger.error(f"❌ Failed to connect to {self.config.name}")
                return False

            logger.info(f"✅ Connected to {self.config.name} (chain {self.config.chain_id})")

            self.price_engine = PriceEngine(self.w3, self.config)

            contract_addr = CONTRACT_ADDRESSES.get(self.chain_key, "")
            if contract_addr:
                self.executor = TxExecutor(
                    w3=self.w3,
                    chain_config=self.config,
                    chain_key=self.chain_key,
                    contract_address=contract_addr,
                    private_key=PRIVATE_KEY,
                    wallet_address=WALLET_ADDRESS,
                    gas_multiplier=GAS_MULTIPLIER,
                    slippage_tolerance=SLIPPAGE_TOLERANCE,
                )
            else:
                logger.warning(
                    f"⚠️ No contract address for {self.config.name} — "
                    f"scanning only (no execution)"
                )

            return True

        except Exception as e:
            logger.error(f"❌ Error connecting to {self.config.name}: {e}")
            return False


# ─── Main Bot ────────────────────────────────────────────────────────────────

class ArbitrageBot:
    def __init__(self):
        self.chains: Dict[str, ChainInstance] = {}
        self.trade_count = 0
        self.next_reset = datetime.now() + timedelta(days=1)
        self.total_profit = {}  # chain -> token -> amount

    def setup(self):
        """Initialize connections to all enabled chains."""
        if not PRIVATE_KEY or not WALLET_ADDRESS:
            logger.error("❌ PRIVATE_KEY and WALLET_ADDRESS are required")
            return False

        logger.info(f"🔑 Wallet: {WALLET_ADDRESS}")
        logger.info(f"📡 Enabled chains: {', '.join(ENABLED_CHAINS)}")
        logger.info(f"{'🧪 DRY RUN MODE' if DRY_RUN else '🔴 LIVE TRADING MODE'}")
        logger.info(f"⚡ Flash loans: {'enabled' if USE_FLASH_LOANS else 'disabled'}")

        any_connected = False
        for chain_key in ENABLED_CHAINS:
            config = CHAINS.get(chain_key)
            if config is None:
                logger.warning(f"⚠️ Unknown chain: {chain_key}")
                continue

            instance = ChainInstance(chain_key, config)
            if instance.connect():
                self.chains[chain_key] = instance
                any_connected = True

        return any_connected

    def reset_daily_counter(self):
        if datetime.now() >= self.next_reset:
            self.trade_count = 0
            self.next_reset = datetime.now() + timedelta(days=1)
            logger.info("🔄 Daily trade counter reset")

    def scan_chain(self, chain_key: str) -> List[ArbOpportunity]:
        """Scan a single chain for opportunities."""
        instance = self.chains.get(chain_key)
        if instance is None or instance.price_engine is None:
            return []

        try:
            opportunities = instance.price_engine.find_opportunities(
                trade_sizes=TRADE_SIZES,
                min_profit_pct=MIN_PROFIT_PCT,
            )
            return opportunities
        except Exception as e:
            logger.error(f"❌ [{chain_key}] Scan error: {e}")
            return []

    def execute_opportunity(
        self, chain_key: str, opp: ArbOpportunity
    ) -> Optional[str]:
        """Execute a single arbitrage opportunity."""
        instance = self.chains.get(chain_key)
        if instance is None or instance.executor is None:
            logger.warning(f"⚠️ No executor for {chain_key}")
            return None

        if DRY_RUN:
            decimals = instance.config.decimals.get(opp.token_a, 18)
            profit_human = opp.net_profit / (10**decimals)
            logger.info(
                f"🧪 [DRY RUN] [{instance.config.name}] Would execute: "
                f"{opp.token_a}->{opp.token_b} | "
                f"Buy: {opp.buy_dex} Sell: {opp.sell_dex} | "
                f"Net profit: {profit_human:.6f} {opp.token_a}"
            )
            return "DRY_RUN"

        if USE_FLASH_LOANS:
            return instance.executor.execute_flash_arb(opp)
        else:
            return instance.executor.execute_direct_arb(opp)

    def run(self):
        """Main loop: scan all chains, execute best opportunities."""
        if not self.setup():
            logger.error("❌ No chains connected. Exiting.")
            return

        logger.info("=" * 60)
        logger.info("🚀 Multi-Chain Arbitrage Bot Started")
        logger.info(f"   Chains: {', '.join(self.chains.keys())}")
        total_pairs = sum(
            len(inst.config.pairs) for inst in self.chains.values()
        )
        total_dexes = sum(
            len(inst.config.dexes) for inst in self.chains.values()
        )
        logger.info(f"   Monitoring {total_pairs} pairs across {total_dexes} DEXes")
        logger.info("=" * 60)

        while True:
            try:
                self.reset_daily_counter()

                if self.trade_count >= MAX_TRADES_PER_DAY:
                    logger.info("📊 Daily trade limit reached, waiting...")
                    time.sleep(60)
                    continue

                all_opportunities: List[Tuple[str, ArbOpportunity]] = []

                for chain_key in self.chains:
                    opps = self.scan_chain(chain_key)
                    for opp in opps:
                        all_opportunities.append((chain_key, opp))

                if not all_opportunities:
                    logger.debug("⚖️ No opportunities found this cycle")
                else:
                    # Sort all opportunities across chains by net profit
                    all_opportunities.sort(
                        key=lambda x: x[1].net_profit, reverse=True
                    )

                    best_chain, best_opp = all_opportunities[0]
                    instance = self.chains[best_chain]
                    decimals = instance.config.decimals.get(best_opp.token_a, 18)
                    profit_human = best_opp.net_profit / (10**decimals)

                    logger.info(
                        f"🏆 Best opportunity: [{instance.config.name}] "
                        f"{best_opp.token_a}->{best_opp.token_b} | "
                        f"Buy: {best_opp.buy_dex} Sell: {best_opp.sell_dex} | "
                        f"Net: {profit_human:.6f} {best_opp.token_a} "
                        f"({best_opp.gross_profit_pct:.3f}%)"
                    )

                    result = self.execute_opportunity(best_chain, best_opp)
                    if result:
                        self.trade_count += 1
                        logger.info(
                            f"📈 Trade #{self.trade_count}/{MAX_TRADES_PER_DAY} today"
                        )

                time.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                logger.info("🛑 Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"❌ Unexpected error in main loop: {e}")
                time.sleep(SCAN_INTERVAL * 2)


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot = ArbitrageBot()
    bot.run()
