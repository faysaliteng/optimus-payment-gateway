"""
Chain registry — the single source of truth for every network the gateway supports.

Each stablecoin "method" maps to: which scanner drives it (EVM getLogs / TON memo),
the token contract, its on-chain decimals, keyless RPC endpoints, and the block-scan
tuning. Amounts are handled EVERYWHERE as integer CENTS (1 USDT = 100 cents) so there
is never a floating-point rounding bug in money math:

    cents = raw_token_units // 10 ** (decimals - 2)

All contracts/decimals/RPCs below are the exact values used by the production Optimus
gateway that has settled real deposits on BSC, Ethereum and Polygon.
"""
from __future__ import annotations

# --- ERC-20 Transfer(address,address,uint256) event topic0 (keccak256) -------
EVM_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# transfer(address,uint256) / balanceOf(address) selectors
TRANSFER_SELECTOR = "a9059cbb"
BALANCEOF_SELECTOR = "70a08231"

# --- Native gas coin per EVM chain ------------------------------------------
NATIVE_COIN = {56: "BNB", 1: "ETH", 137: "POL"}


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
