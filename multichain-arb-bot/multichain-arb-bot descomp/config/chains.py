"""
Chain Configurations — Arbitrum & Base
========================================
Token addresses, DEX routers, Aave providers, and pair definitions
for multi-chain multi-pair arbitrage.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class DexConfig:
    name: str
    router: str
    dex_type: str  # "v2" or "v3"
    quoter: str = ""  # Only for V3 DEXes
    factory: str = ""  # Only for V2 DEXes


@dataclass
class ChainConfig:
    chain_id: int
    name: str
    rpc_env_key: str  # Key in .env for the RPC URL
    aave_pool_provider: str
    weth: str
    tokens: Dict[str, str]
    decimals: Dict[str, int]
    dexes: Dict[str, DexConfig]
    pairs: List[Tuple[str, str]]  # Token pairs to monitor


# ─── Arbitrum One ────────────────────────────────────────────────────────────

ARBITRUM_CONFIG = ChainConfig(
    chain_id=42161,
    name="Arbitrum",
    rpc_env_key="ARBITRUM_RPC",
    aave_pool_provider="0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb",
    weth="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    tokens={
        "WETH":  "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "USDC":  "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # Native USDC
        "USDC.e": "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",  # Bridged USDC
        "USDT":  "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "WBTC":  "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        "ARB":   "0x912CE59144191C1204E64559FE8253a0e49E6548",
        "MAGIC": "0x539bdE0d7Dbd336b79148AA742883198BBF60342",
        "GMX":   "0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a",
        "PENDLE":"0x0c880f6761F1af8d9Aa9C466984b80DAb9a8c9e8",
        "DAI":   "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
        "LINK":  "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4",
    },
    decimals={
        "WETH": 18, "USDC": 6, "USDC.e": 6, "USDT": 6,
        "WBTC": 8, "ARB": 18, "MAGIC": 18, "GMX": 18,
        "PENDLE": 18, "DAI": 18, "LINK": 18,
    },
    dexes={
        "uniswap_v3": DexConfig(
            name="Uniswap V3",
            router="0xE592427A0AEce92De3Edee1F18E0157C05861564",
            quoter="0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
            dex_type="v3",
        ),
        "sushiswap": DexConfig(
            name="SushiSwap",
            router="0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
            factory="0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
            dex_type="v2",
        ),
        "camelot": DexConfig(
            name="Camelot",
            router="0xc873fEcbd354f5A56E00E710B90EF4201db2448d",
            factory="0x6EcCab422D763aC031210895C81787E87B43A652",
            dex_type="v2",
        ),
    },
    pairs=[
        ("WETH", "USDC"),
        ("WETH", "USDT"),
        ("WETH", "WBTC"),
        ("WETH", "ARB"),
        ("WETH", "MAGIC"),
        ("WETH", "GMX"),
        ("WETH", "PENDLE"),
        ("WETH", "LINK"),
        ("USDC", "USDT"),
        ("USDC", "ARB"),
        ("USDC", "MAGIC"),
        ("ARB",  "USDC"),
        ("WBTC", "USDC"),
    ],
)

# ─── Base ────────────────────────────────────────────────────────────────────

BASE_CONFIG = ChainConfig(
    chain_id=8453,
    name="Base",
    rpc_env_key="BASE_RPC",
    aave_pool_provider="0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D",
    weth="0x4200000000000000000000000000000000000006",
    tokens={
        "WETH":  "0x4200000000000000000000000000000000000006",
        "USDC":  "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "USDbC": "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",  # Bridged USDC
        "DAI":   "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
        "cbETH": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22",
        "AERO":  "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
        "BRETT": "0x532f27101965dd16442E59d40670FaF5eBB142E4",
        "DEGEN": "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed",
        "TOSHI": "0xAC1Bd2486aAf3B5C0fc3Fd868558b082a531B2B4",
    },
    decimals={
        "WETH": 18, "USDC": 6, "USDbC": 6, "DAI": 18,
        "cbETH": 18, "AERO": 18, "BRETT": 18, "DEGEN": 18, "TOSHI": 18,
    },
    dexes={
        "uniswap_v3": DexConfig(
            name="Uniswap V3",
            router="0x2626664c2603336E57B271c5C0b26F421741e481",
            quoter="0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",
            dex_type="v3",
        ),
        "sushiswap": DexConfig(
            name="SushiSwap V2",
            router="0x6BDED42c6DA8FBf0d2bA55B2fa120C5e0c8D7891",
            factory="0x71524B4f93c58fcbF659783284E38825f0622859",
            dex_type="v2",
        ),
        "aerodrome": DexConfig(
            name="Aerodrome",
            router="0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43",
            factory="0x420DD381b31aEf6683db6B902084cB0FFECe40Da",
            dex_type="v2",
        ),
    },
    pairs=[
        ("WETH", "USDC"),
        ("WETH", "cbETH"),
        ("WETH", "DAI"),
        ("WETH", "AERO"),
        ("WETH", "BRETT"),
        ("WETH", "DEGEN"),
        ("USDC", "DAI"),
        ("USDC", "USDbC"),
        ("USDC", "AERO"),
        ("cbETH", "USDC"),
    ],
)

# ─── Registry ────────────────────────────────────────────────────────────────

CHAINS: Dict[str, ChainConfig] = {
    "arbitrum": ARBITRUM_CONFIG,
    "base": BASE_CONFIG,
}

# Router IDs used in the smart contract mapping
# These must match the order you deploy with
ROUTER_IDS = {
    "arbitrum": {
        "uniswap_v3": 0,
        "sushiswap": 1,
        "camelot": 2,
    },
    "base": {
        "uniswap_v3": 0,
        "sushiswap": 1,
        "aerodrome": 2,
    },
}
