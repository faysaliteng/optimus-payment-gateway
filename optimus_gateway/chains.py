"""
Chain registry — the single source of truth for every network the gateway supports.

Each stablecoin "method" maps to: which scanner drives it (EVM getLogs / TON memo),
the token contract, its on-chain decimals, keyless RPC endpoints, and the block-scan
tuning. Amounts are handled EVERYWHERE as integer CENTS (1 USDT = 100 cents) so there
is never a floating-point rounding bug in money math:

    cents = raw_token_units // 10 ** (decimals - 2)

The BSC, Ethereum, Polygon and TON entries are the exact values used by the production
Optimus gateway that has settled real deposits. Arbitrum, Optimism, Base and Avalanche
use the same keyless-getLogs machinery; their contracts were cross-verified against each
chain's official explorer and Circle's published USDC address list.
"""
from __future__ import annotations

# --- ERC-20 Transfer(address,address,uint256) event topic0 (keccak256) -------
EVM_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# transfer(address,uint256) / balanceOf(address) selectors
TRANSFER_SELECTOR = "a9059cbb"
BALANCEOF_SELECTOR = "70a08231"

# --- Native gas coin per EVM chain ------------------------------------------
NATIVE_COIN = {56: "BNB", 1: "ETH", 137: "POL", 42161: "ETH", 10: "ETH", 8453: "ETH", 43114: "AVAX"}


# --- The registry -----------------------------------------------------------
# scanner: "evm" (getLogs) | "ton_memo"
# decimals: token decimals; cents divisor is 10**(decimals-2)
# tokens: {SYMBOL: contract} — the coins watched on this network
# rpcs: keyless public endpoints (rotated). Public BSC dataseed nodes BLOCK
#       eth_getLogs, so BSC uses onfinality/bloXroute which allow it.
CHAINS: dict[str, dict] = {
    "usdt_bep20": {
        "label": "USDT (BEP20 / BSC)",
        "short": "BEP20",
        "scanner": "evm",
        "chain_id": 56,
        "decimals": 18,
        "tokens": {
            "USDT": "0x55d398326f99059ff775485246999027b3197955",
            "USDC": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
        },
        "rpcs": [
            "https://bnb.api.onfinality.io/public",
            "https://bsc.rpc.blxrbdn.com",
            "https://bsc-dataseed.binance.org",
        ],
        "rpc_setting": "bep20_gateway_rpc",
        "cursor_key": "bep20_watch_last_block",
        "max_span": 80,
        "initial_lookback": 240,
        "explorer": "https://bscscan.com/tx/",
    },
    "usdt_erc20": {
        "label": "USDT (ERC20 / Ethereum)",
        "short": "ERC20",
        "scanner": "evm",
        "chain_id": 1,
        "decimals": 6,
        "tokens": {
            "USDT": "0xdac17f958d2ee523a2206206994597c13d831ec7",
            "USDC": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        },
        "rpcs": [
            "https://ethereum-rpc.publicnode.com",
            "https://eth.drpc.org",
            "https://rpc.ankr.com/eth",
            "https://cloudflare-eth.com",
        ],
        "rpc_setting": "erc20_gateway_rpc",
        "cursor_key": "erc20_watch_last_block",
        "max_span": 500,
        "initial_lookback": 200,
        "explorer": "https://etherscan.io/tx/",
    },
    "usdt_polygon": {
        "label": "USDT (Polygon)",
        "short": "Polygon",
        "scanner": "evm",
        "chain_id": 137,
        "decimals": 6,
        "tokens": {
            "USDT": "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
            "USDC": "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
            "USDC.e": "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
        },
        "rpcs": [
            "https://polygon-bor-rpc.publicnode.com",
            "https://polygon.drpc.org",
            "https://polygon-rpc.com",
            "https://rpc.ankr.com/polygon",
        ],
        "rpc_setting": "polygon_gateway_rpc",
        "cursor_key": "polygon_watch_last_block",
        "max_span": 20,
        "initial_lookback": 60,
        "explorer": "https://polygonscan.com/tx/",
    },
    "usdt_arbitrum": {
        "label": "USDT/USDC (Arbitrum)",
        "short": "Arbitrum",
        "scanner": "evm",
        "chain_id": 42161,
        "decimals": 6,
        "tokens": {
            "USDT": "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
            "USDC": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
            "USDC.e": "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",
        },
        "rpcs": [
            "https://arbitrum-one-rpc.publicnode.com",
            "https://arb1.arbitrum.io/rpc",
            "https://arbitrum.drpc.org",
        ],
        "rpc_setting": "arbitrum_gateway_rpc",
        "cursor_key": "arbitrum_watch_last_block",
        "max_span": 1000,
        "initial_lookback": 1000,
        "explorer": "https://arbiscan.io/tx/",
    },
    "usdt_optimism": {
        "label": "USDT/USDC (Optimism)",
        "short": "Optimism",
        "scanner": "evm",
        "chain_id": 10,
        "decimals": 6,
        "tokens": {
            "USDT": "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58",
            "USDC": "0x0b2c639c533813f4aa9d7837caf62653d097ff85",
            "USDC.e": "0x7f5c764cbc14f9669b88837ca1490cca17c31607",
        },
        "rpcs": [
            "https://optimism-rpc.publicnode.com",
            "https://mainnet.optimism.io",
            "https://optimism.drpc.org",
        ],
        "rpc_setting": "optimism_gateway_rpc",
        "cursor_key": "optimism_watch_last_block",
        "max_span": 1000,
        "initial_lookback": 500,
        "explorer": "https://optimistic.etherscan.io/tx/",
    },
    "usdt_base": {
        "label": "USDT/USDC (Base)",
        "short": "Base",
        "scanner": "evm",
        "chain_id": 8453,
        "decimals": 6,
        "tokens": {
            "USDC": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "USDT": "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2",
            "USDbC": "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",
        },
        "rpcs": [
            "https://base-rpc.publicnode.com",
            "https://mainnet.base.org",
            "https://base.drpc.org",
        ],
        "rpc_setting": "base_gateway_rpc",
        "cursor_key": "base_watch_last_block",
        "max_span": 1000,
        "initial_lookback": 500,
        "explorer": "https://basescan.org/tx/",
    },
    "usdt_avalanche": {
        "label": "USDT/USDC (Avalanche C-Chain)",
        "short": "Avalanche",
        "scanner": "evm",
        "chain_id": 43114,
        "decimals": 6,
        "tokens": {
            "USDT": "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7",
            "USDC": "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e",
            "USDT.e": "0xc7198437980c041c805a1edcba50c1ce5db95118",
            "USDC.e": "0xa7d7079b0fead91f3e65f86e8915cb59c1a4c664",
        },
        "rpcs": [
            "https://avalanche-c-chain-rpc.publicnode.com",
            "https://api.avax.network/ext/bc/C/rpc",
            "https://avalanche.drpc.org",
        ],
        "rpc_setting": "avalanche_gateway_rpc",
        "cursor_key": "avalanche_watch_last_block",
        "max_span": 2000,
        "initial_lookback": 500,
        "explorer": "https://snowtrace.io/tx/",
    },
    "usdt_ton": {
        "label": "USDT (TON)",
        "short": "TON",
        "scanner": "ton_memo",
        "decimals": 6,
        "jetton_master": "0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe",
        "toncenter": "https://toncenter.com/api/v3",
        "explorer": "https://tonviewer.com/transaction/",
    },
}

# The EVM chains that can ALSO be swept / wrong-network-recovered. Every EVM chain
# shares the same address space, so a per-order address derived once works on all of
# them — that's what makes wrong-network recovery possible.
EVM_METHODS = [m for m, c in CHAINS.items() if c.get("scanner") == "evm"]


# ---------------------------------------------------------------------------
#  Fake-token protection — the ALLOWLIST of real stablecoin token contracts.
#
#  The gateway only ever scans (watcher getLogs), credits, or sweeps the EXACT token
#  contracts listed in the CHAINS registry above. A scammer can deploy a token they
#  NAME "USDT" / "USDC" / "BSC-USD" at some other contract and send it to a gateway
#  address, but because every scan and every balance/sweep call is filtered by these
#  contract addresses, such a fake token is never seen, never credited, and never swept.
#  These derived structures make that guarantee explicit, testable, and reusable so any
#  code path that ever takes a contract from outside input can reject non-real tokens.
#  The registry IS the single source of truth: add a real coin there and it's covered
#  everywhere; anything not there is treated as fake.
# ---------------------------------------------------------------------------
REAL_STABLECOIN_CONTRACTS: frozenset = frozenset(
    contract.lower()
    for chain in CHAINS.values()
    if chain.get("scanner") == "evm"
    for contract in chain.get("tokens", {}).values()
)

# contract (lowercased) -> {symbol, method, chain_id, decimals} for reverse lookup.
TOKEN_BY_CONTRACT: dict = {
    contract.lower(): {
        "symbol": sym,
        "method": method,
        "chain_id": chain.get("chain_id"),
        "decimals": chain.get("decimals"),
    }
    for method, chain in CHAINS.items()
    if chain.get("scanner") == "evm"
    for sym, contract in chain.get("tokens", {}).items()
}


def is_real_stablecoin(contract: str) -> bool:
    """True if `contract` is a real USDT/USDC (or supported bridged variant) on any chain
    the gateway watches. Anything else is an unknown/scam token — never to be credited."""
    return str(contract or "").strip().lower() in REAL_STABLECOIN_CONTRACTS


def stablecoins_for_chain(method: str) -> dict:
    """{SYMBOL: contract} of the real stablecoins on one chain (empty for TON/unknown)."""
    return dict(CHAINS.get(method, {}).get("tokens", {}))


def is_evm(method: str) -> bool:
    return CHAINS.get(method, {}).get("scanner") == "evm"


def cents_divisor(method: str) -> int:
    """10**(decimals-2): raw token base-units per integer cent."""
    return 10 ** (int(CHAINS[method]["decimals"]) - 2)


def to_cents(method: str, raw_units: int) -> int:
    return int(raw_units) // cents_divisor(method)


def to_raw(method: str, cents: int) -> int:
    return int(cents) * cents_divisor(method)


def chain_id(method: str) -> int:
    return int(CHAINS[method]["chain_id"])


def native_coin(method: str) -> str:
    return NATIVE_COIN.get(chain_id(method), "ETH")


def to_topic_address(addr: str) -> str:
    """20-byte address -> 32-byte left-zero-padded topic (for the Transfer TO filter)."""
    return "0x" + "0" * 24 + addr.lower().replace("0x", "")
