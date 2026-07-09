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
| `usdt_polygon` | Polygon, chain id **137** | USDT, USDC, USDC.e | 6 | **POL** | EVM `getLogs` | [polygonscan.com](https://polygonscan.com/tx/) |
| `usdt_arbitrum` | Arbitrum One, chain id **42161** | USDT, USDC, USDC.e | 6 | **ETH** | EVM `getLogs` | [arbiscan.io](https://arbiscan.io/tx/) |
| `usdt_optimism` | OP Mainnet, chain id **10** | USDT, USDC, USDC.e | 6 | **ETH** | EVM `getLogs` | [optimistic.etherscan.io](https://optimistic.etherscan.io/tx/) |
| `usdt_base` | Base, chain id **8453** | USDC, USDT, USDbC | 6 | **ETH** | EVM `getLogs` | [basescan.org](https://basescan.org/tx/) |
| `usdt_erc20` | Ethereum (ERC20), chain id **1** | USDT, USDC | 6 | **ETH** | EVM `getLogs` | [etherscan.io](https://etherscan.io/tx/) |
| `usdt_avalanche` | Avalanche C-Chain, chain id **43114** | USDT, USDC, USDT.e, USDC.e | 6 | **AVAX** | EVM `getLogs` | [snowtrace.io](https://snowtrace.io/tx/) |
| `usdt_ton` | TON | USDT (jetton) | 6 | **TON** | TON memo (toncenter) | [tonviewer.com](https://tonviewer.com/transaction/) |

> All EVM chains share the same secp256k1 address space, so **one xpub (or one dedicated
> wallet) covers BSC, Ethereum, Polygon, Arbitrum, Optimism, Base and Avalanche at once**,
> and a buyer who pays on the wrong EVM network still sends to an address you control — see
> wrong-network recovery in [`sweeper.py`](../optimus_gateway/sweeper.py).

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

### Arbitrum One — `usdt_arbitrum`  ·  decimals **6**  ·  gas **ETH**  ·  chain id **42161**
| Token | Contract |
|---|---|
| USDT | `0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9` |
| USDC (native) | `0xaf88d065e77c8cc2239327c5edb3a432268e5831` |
| USDC.e (bridged) | `0xff970a61a04b1ca14834a43f5de4533ebddb5cc8` |

### OP Mainnet (Optimism) — `usdt_optimism`  ·  decimals **6**  ·  gas **ETH**  ·  chain id **10**
| Token | Contract |
|---|---|
| USDT | `0x94b008aa00579c1307b0ef2c499ad98a8ce58e58` |
| USDC (native) | `0x0b2c639c533813f4aa9d7837caf62653d097ff85` |
| USDC.e (bridged) | `0x7f5c764cbc14f9669b88837ca1490cca17c31607` |

### Base — `usdt_base`  ·  decimals **6**  ·  gas **ETH**  ·  chain id **8453**
| Token | Contract |
|---|---|
| USDC (native) | `0x833589fcd6edb6e08f4c7c32d4f71b54bda02913` |
| USDT (bridged) | `0xfde4c96c8593536e31f229ea8f37b2ada2699bb2` |
| USDbC (bridged) | `0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca` |

### Avalanche C-Chain — `usdt_avalanche`  ·  decimals **6**  ·  gas **AVAX**  ·  chain id **43114**
| Token | Contract |
|---|---|
| USDT (native) | `0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7` |
| USDC (native) | `0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e` |
| USDT.e (bridged) | `0xc7198437980c041c805a1edcba50c1ce5db95118` |
| USDC.e (bridged) | `0xa7d7079b0fead91f3e65f86e8915cb59c1a4c664` |

> Every contract above was cross-verified by two independent lookups against the chain's
> official explorer (Arbiscan / Optimistic Etherscan / Basescan / Snowtrace) and Circle's
> published USDC address list. **Base has no native Tether-issued USDT** — the listed one
> is the widely-used L2-bridged USDT.

### USDT (TON) — `usdt_ton`  ·  decimals **6**  ·  gas **TON**
| Field | Value |
|---|---|
| Jetton master | `0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe` |
| toncenter API | `https://toncenter.com/api/v3` |

> **Decimals gotcha:** BSC's USDT/USDC use **18** decimals; **every other chain here
> (Ethereum, Polygon, Arbitrum, Optimism, Base, Avalanche, TON) uses 6**. The
> `cents_divisor` handles this automatically (`10**(decimals-2)` → `1e16` on BSC, `1e4`
> everywhere else), so amounts are always compared in cents — no per-chain math anywhere
> in your code.

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
| `usdt_polygon` | `https://polygon-bor-rpc.publicnode.com`<br>`https://polygon.drpc.org`<br>`https://polygon-rpc.com`<br>`https://rpc.ankr.com/polygon` |
| `usdt_arbitrum` | `https://arbitrum-one-rpc.publicnode.com`<br>`https://arb1.arbitrum.io/rpc`<br>`https://arbitrum.drpc.org` |
| `usdt_optimism` | `https://optimism-rpc.publicnode.com`<br>`https://mainnet.optimism.io`<br>`https://optimism.drpc.org` |
| `usdt_base` | `https://base-rpc.publicnode.com`<br>`https://mainnet.base.org`<br>`https://base.drpc.org` |
| `usdt_erc20` | `https://ethereum-rpc.publicnode.com`<br>`https://eth.drpc.org`<br>`https://rpc.ankr.com/eth`<br>`https://cloudflare-eth.com` |
| `usdt_avalanche` | `https://avalanche-c-chain-rpc.publicnode.com`<br>`https://api.avax.network/ext/bc/C/rpc`<br>`https://avalanche.drpc.org` |
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
| `usdt_polygon` | `polygon_gateway_rpc` |
| `usdt_arbitrum` | `arbitrum_gateway_rpc` |
| `usdt_optimism` | `optimism_gateway_rpc` |
| `usdt_base` | `base_gateway_rpc` |
| `usdt_erc20` | `erc20_gateway_rpc` |
| `usdt_avalanche` | `avalanche_gateway_rpc` |

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
| Arbitrum (`usdt_arbitrum`) | 3–5 | L2 with fast soft-finality; sequencer-ordered |
| Optimism (`usdt_optimism`) | 3–5 | L2 with fast soft-finality; sequencer-ordered |
| Base (`usdt_base`) | 3–5 | L2 with fast soft-finality; sequencer-ordered |
| Ethereum (`usdt_erc20`) | 3–6 | ~12 s blocks; each confirm is worth more |
| Avalanche (`usdt_avalanche`) | 2–4 | sub-second finality (Snowman consensus) |

**TON** does not use this setting: `scan_ton` credits jetton transfers that toncenter
returns as completed (aborted transfers are skipped).

Related watcher tuning (see `.env.example` / `config.py`): `OPG_RESCAN_OVERLAP` (re-scan
cushion), `OPG_MAX_CATCHUP_BLOCKS`, `OPG_WATCH_POLL_SECONDS`, and each chain's per-call
`max_span` / `initial_lookback` in `chains.py`.

---

## 6. Recipe — add a *new* EVM chain

> **Base, Arbitrum, Optimism and Avalanche already ship built-in** (see sections 1–2) —
> you don't need this recipe for them, just list them in `OPG_ENABLED_METHODS`. This
> section shows the pattern for adding a chain that *isn't* in the registry yet (e.g.
> Linea, Scroll, Mantle, BSC-testnet, …).

Adding an EVM chain is two small edits. The example below uses **Base**'s values only to
show the shape of a complete entry; substitute real, explorer-verified values for whatever
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
NATIVE_COIN = {56: "BNB", 1: "ETH", 137: "POL", 42161: "ETH",
               10: "ETH", 8453: "ETH", 43114: "AVAX"}   # add your chain_id: "COIN"
```

`EVM_METHODS`, `is_evm()`, `cents_divisor()`, `chain_id()`, and the watcher pick up the
new method automatically — no other code changes are needed to **watch and credit** it.

### Step 2 — add a `GAS` entry in `optimus_gateway/sweeper.py` (only if you auto-sweep)

The sweeper needs per-chain gas limits + a gas-price floor/cap (in **wei**), keyed by
`chain_id`:

```python
GAS = {
    56:    {"token": 90_000,    "native": 21_000,    "min": 1_000_000_000, "max": 5_000_000_000},    # BSC
    1:     {"token": 70_000,    "native": 21_000,    "min": 100_000_000,   "max": 60_000_000_000},   # Ethereum
    137:   {"token": 70_000,    "native": 21_000,    "min": 100_000_000,   "max": 600_000_000_000},  # Polygon
    42161: {"token": 3_000_000, "native": 1_000_000, "min": 10_000_000,    "max": 20_000_000_000},   # Arbitrum
    10:    {"token": 300_000,   "native": 40_000,    "min": 1_000_000,     "max": 20_000_000_000},   # Optimism
    8453:  {"token": 300_000,   "native": 40_000,    "min": 1_000_000,     "max": 20_000_000_000},   # Base
    43114: {"token": 200_000,   "native": 30_000,    "min": 1_000_000_000, "max": 300_000_000_000},  # Avalanche
    # your_chain_id: {"token": ..., "native": ..., "min": ..., "max": ...},
}
```

- `token` — gas units for an ERC-20 transfer (70k is typical; BSC uses 90k). **Arbitrum**
  reports inflated L2 gas *units* (priced very low), so its limit is millions — you still
  only pay the actual gas used, the limit is just a ceiling.
- `native` — gas units for a plain native transfer (21k on most chains; higher on the
  OP-stack / Arbitrum L2s).
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
