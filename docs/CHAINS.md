# Supported chains, tokens & networks

Everything here is the literal content of
[`optimus_gateway/chains.py`](../optimus_gateway/chains.py) (the single source of truth)
plus the gas config in [`optimus_gateway/sweeper.py`](../optimus_gateway/sweeper.py).
If you change a contract, decimals, or RPC, change it there and this table follows.

Money is handled **everywhere as integer cents** (`1 USDT = 100 cents`) so there is never
a floating-point rounding bug. The conversion is:

```
cents = raw_token_units // 10 ** (decimals - 2)      # cents_divisor(method)
```

---

## 1. Supported methods

A "method" is the key you pass as `method` to the API / `create_payment(...)` and list
in `OPG_ENABLED_METHODS`.

| Method key | Network | Token(s) watched | Decimals | Native gas coin | Scanner | Explorer |
|---|---|---|---|---|---|---|
| `usdt_bep20` | BSC (BEP20), chain id **56** | USDT, USDC | 18 | **BNB** | EVM `getLogs` | [bscscan.com](https://bscscan.com/tx/) |
| `usdt_erc20` | Ethereum (ERC20), chain id **1** | USDT, USDC | 6 | **ETH** | EVM `getLogs` | [etherscan.io](https://etherscan.io/tx/) |
| `usdt_polygon` | Polygon, chain id **137** | USDT, USDC, USDC.e | 6 | **POL** | EVM `getLogs` | [polygonscan.com](https://polygonscan.com/tx/) |
| `usdt_ton` | TON | USDT (jetton) | 6 | **TON** | TON memo (toncenter) | [tonviewer.com](https://tonviewer.com/transaction/) |

> All EVM chains share the same secp256k1 address space, so **one xpub (or one dedicated
> wallet) covers BSC, Ethereum and Polygon at once**, and a buyer who pays on the wrong
> EVM network still sends to an address you control — see wrong-network recovery in
> [`sweeper.py`](../optimus_gateway/sweeper.py).

---

## 2. Exact token contracts + decimals

These are the exact production values from `chains.py`. **Always verify a contract on
the chain's explorer before sending real money.**

### USDT (BEP20 / BSC) — `usdt_bep20`  ·  decimals **18**  ·  gas **BNB**
| Token | Contract |
|---|---|
| USDT | `0x55d398326f99059ff775485246999027b3197955` |
| USDC | `0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d` |

### USDT (ERC20 / Ethereum) — `usdt_erc20`  ·  decimals **6**  ·  gas **ETH**
| Token | Contract |
|---|---|
| USDT | `0xdac17f958d2ee523a2206206994597c13d831ec7` |
| USDC | `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` |

### USDT (Polygon) — `usdt_polygon`  ·  decimals **6**  ·  gas **POL**
| Token | Contract |
|---|---|
| USDT | `0xc2132d05d31c914a87c6611c10748aeb04b58e8f` |
| USDC (native) | `0x3c499c542cef5e3811e1192ce70d8cc03d5c3359` |
| USDC.e (bridged) | `0x2791bca1f2de4661ed88a30c99a7a9449aa84174` |

### USDT (TON) — `usdt_ton`  ·  decimals **6**  ·  gas **TON**
| Field | Value |
|---|---|
| Jetton master | `0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe` |
| toncenter API | `https://toncenter.com/api/v3` |

> **Decimals gotcha:** BSC's USDT/USDC use **18** decimals; Ethereum's and Polygon's use
> **6**. The `cents_divisor` handles this automatically (`10**(decimals-2)` → `1e16` on
> BSC, `1e4` on ETH/Polygon), so amounts are always compared in cents.

---

## 3. USDC support

USDC is a first-class citizen alongside USDT on every EVM chain, controlled by one flag:

```ini
OPG_ACCEPT_USDC=true     # default true (config.ACCEPT_USDC)
```

- When `true`, the watcher watches **both** the USDT and USDC contracts on each EVM chain
  (and Polygon's USDC.e too), and the sweeper forwards whichever token arrives.
- When `false`, only USDT is watched/swept.
- **TON has no USDC** in this gateway — only the USDT jetton.

A buyer is always quoted "USDT" in the checkout UI, but a same-value USDC payment to the
order's address credits identically (1 USDC = 1 USDT = 100 cents).

---

## 4. Keyless RPC endpoints (and how to override them)

Every EVM chain ships with a rotated list of **keyless, public** RPC endpoints — no API
key, no signup. The watcher/sweeper try them in order and move on if one fails.

| Method | Default RPCs (in order) |
|---|---|
| `usdt_bep20` | `https://bnb.api.onfinality.io/public`<br>`https://bsc.rpc.blxrbdn.com`<br>`https://bsc-dataseed.binance.org` |
| `usdt_erc20` | `https://ethereum-rpc.publicnode.com`<br>`https://eth.drpc.org`<br>`https://rpc.ankr.com/eth`<br>`https://cloudflare-eth.com` |
| `usdt_polygon` | `https://polygon-bor-rpc.publicnode.com`<br>`https://polygon.drpc.org`<br>`https://polygon-rpc.com`<br>`https://rpc.ankr.com/polygon` |
| `usdt_ton` | toncenter v3 (`https://toncenter.com/api/v3`); optional key via `OPG_TONCENTER_API_KEY` |

> **Why not the plain BSC dataseed for everything?** Most public BSC dataseed nodes
> **block `eth_getLogs`**, which the watcher relies on. That's why BEP20 defaults to
> OnFinality + bloXroute first, with the dataseed as a last resort.

### Overriding EVM RPCs per chain (DB setting)

RPC overrides are read from the **`settings` table** in the database, per chain, via the
key in each chain's `rpc_setting`. Your custom URLs are **prepended** to the defaults
(so they're tried first, and the public list remains as a fallback):

| Method | DB setting key |
|---|---|
| `usdt_bep20` | `bep20_gateway_rpc` |
| `usdt_erc20` | `erc20_gateway_rpc` |
| `usdt_polygon` | `polygon_gateway_rpc` |

Set one (comma- or newline-separated list of `http(s)://…` URLs):

```bash
python -c "from optimus_gateway import db; db.set_setting('bep20_gateway_rpc', \
  'https://your-paid-bsc-node.example/rpc,https://backup-bsc.example/rpc')"
```

Read it back:

```bash
python -c "from optimus_gateway import db; print(db.get_setting('bep20_gateway_rpc'))"
```

> **Note:** RPC overrides are **DB-setting-based**, not environment variables — there is
> no `OPG_*` env var for per-chain RPCs (only the URLs above hard-coded in `chains.py`
> plus whatever you store in the `settings` table). TON is the exception: its endpoint is
> in `chains.py` and it takes an optional key from `OPG_TONCENTER_API_KEY`. If you run the
> optional admin dashboard, these RPC settings are editable from its UI.

---

## 5. Confirmations before crediting

The number of block confirmations required before an EVM payment is credited is a single
global knob (from `config.py`), applied to **every** EVM chain:

```ini
OPG_MIN_CONFIRMATIONS=3     # default 3; clamped to the range 1..50
```

In the watcher: `confirmed_to = latest_block - OPG_MIN_CONFIRMATIONS`, so only transfers
buried at least that deep are credited. Because the block cursor never advances past
un-scanned or errored blocks, raising this value is always safe (worst case is a re-scan,
which the txid registry makes harmless).

**Recommended values** (set `OPG_MIN_CONFIRMATIONS` to the highest you need across your
enabled chains):

| Chain | Suggested confirmations | Rationale |
|---|---|---|
| BSC (`usdt_bep20`) | 12–15 | ~0.75 s blocks; more confirms = same wall-clock safety |
| Polygon (`usdt_polygon`) | 20–30 | fast blocks + occasional reorgs |
| Ethereum (`usdt_erc20`) | 3–6 | ~12 s blocks; each confirm is worth more |

**TON** does not use this setting: `scan_ton` credits jetton transfers that toncenter
returns as completed (aborted transfers are skipped).

Related watcher tuning (see `.env.example` / `config.py`): `OPG_RESCAN_OVERLAP` (re-scan
cushion), `OPG_MAX_CATCHUP_BLOCKS`, `OPG_WATCH_POLL_SECONDS`, and each chain's per-call
`max_span` / `initial_lookback` in `chains.py`.

---

## 6. Recipe — add a new EVM chain (e.g. Base, Arbitrum, Avalanche)

Adding an EVM chain is two small edits. Example: **Base** (chain id `8453`, native gas
`ETH`, USDC/USDT with 6 decimals). Substitute real, explorer-verified values for any
chain you add.

### Step 1 — add an entry to `CHAINS` in `optimus_gateway/chains.py`

```python
"usdt_base": {
    "label": "USDT (Base)",
    "short": "Base",
    "scanner": "evm",
    "chain_id": 8453,
    "decimals": 6,                       # cents divisor = 10**(6-2) = 1e4
    "tokens": {
        "USDT": "0x...verify_on_basescan...",   # look up the real contracts
        "USDC": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
    },
    "rpcs": [                            # keyless public endpoints, tried in order
        "https://base-rpc.publicnode.com",
        "https://base.drpc.org",
        "https://mainnet.base.org",
    ],
    "rpc_setting": "base_gateway_rpc",   # DB override key (see section 4)
    "cursor_key": "base_watch_last_block",
    "max_span": 500,                     # blocks per getLogs call (tune to the RPC)
    "initial_lookback": 200,
    "explorer": "https://basescan.org/tx/",
},
```

Also add the chain's native gas coin to `NATIVE_COIN` in the same file (skip if the chain
uses ETH and you're happy with the `"ETH"` fallback in `native_coin()`):

```python
NATIVE_COIN = {56: "BNB", 1: "ETH", 137: "POL", 8453: "ETH"}
```

`EVM_METHODS`, `is_evm()`, `cents_divisor()`, `chain_id()`, and the watcher pick up the
new method automatically — no other code changes are needed to **watch and credit** it.

### Step 2 — add a `GAS` entry in `optimus_gateway/sweeper.py` (only if you auto-sweep)

The sweeper needs per-chain gas limits + a gas-price floor/cap (in **wei**), keyed by
`chain_id`:

```python
GAS = {
    56:   {"token": 90_000, "native": 21_000, "min": 1_000_000_000,  "max": 5_000_000_000},
    1:    {"token": 70_000, "native": 21_000, "min": 100_000_000,    "max": 60_000_000_000},
    137:  {"token": 70_000, "native": 21_000, "min": 100_000_000,    "max": 600_000_000_000},
    8453: {"token": 70_000, "native": 21_000, "min": 10_000_000,     "max": 5_000_000_000},  # Base
}
```

- `token` — gas units for an ERC-20 transfer (70k is typical; BSC uses 90k).
- `native` — gas units for a plain native transfer (21k).
- `min` / `max` — gas-price floor and ceiling in wei; `evm.gas_price()` clamps the live
  price into this band. Size it from the chain's typical gas price.

> If a method is enabled without a matching `GAS[chain_id]` entry, watching/crediting
> still works, but **sweeping will `KeyError`**. Add the `GAS` row before enabling
> auto-sweep on a new chain.

### Step 3 — enable it

```ini
OPG_ENABLED_METHODS=usdt_bep20,usdt_polygon,usdt_base
```

Fund the gas-tank address (dedicated-wallet `index 0`) with a little native coin **on the
new chain** if you auto-sweep, then restart. Verify reachability at `/health`.

---

## 7. Where each fact lives

| Fact | File |
|---|---|
| Method keys, contracts, decimals, RPCs, explorers | `optimus_gateway/chains.py` (`CHAINS`) |
| Native gas coin per chain id | `optimus_gateway/chains.py` (`NATIVE_COIN`) |
| Per-chain gas limits & price band | `optimus_gateway/sweeper.py` (`GAS`) |
| USDC toggle, confirmations, RPC-override plumbing | `optimus_gateway/config.py`, `watcher.py`, `sweeper.py` |
| TON jetton master + toncenter endpoint | `optimus_gateway/chains.py`, `optimus_gateway/ton.py` |
