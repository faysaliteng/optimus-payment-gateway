# Architecture

How the Optimus Payment Gateway turns "quote an amount" into "money in your cold
wallet" — every component, the end-to-end payment lifecycle, the two attribution
modes, the exact-cents money model, the crash-safe block cursor, idempotent
crediting, accumulation/overpay handling, and the gas-tank sweeper with
wrong-network recovery.

Everything here maps to real code. File references are relative to the repo root;
function names are exact.

---

## 1. Component map

```
                         ┌──────────────────────────────────────────────┐
   merchant app          │              Optimus Payment Gateway          │
  ┌────────────┐  HTTP   │  ┌──────────┐   ┌───────────┐   ┌───────────┐ │
  │ bot / shop ├────────►│  │ server/  │──►│ gateway.py│──►│   db.py   │ │
  │            │◄────────┤  │ app.py   │   │ create_/  │   │  (ledger) │ │
  └─────┬──────┘ webhook │  │ (FastAPI)│   │ get_paymt │   └─────┬─────┘ │
        │                │  └────┬─────┘   └─────┬─────┘         │       │
        │                │       │ startup       │ derive addr   │       │
        │                │  ┌────▼────────────────▼──┐   ┌───────▼─────┐ │
        │                │  │   server/workers.py    │   │ hdwallet.py │ │
        │                │  │  watcher · sweeper ·   │   │  BIP32/44   │ │
        │                │  │  webhook loops (threads)│   └───────┬─────┘ │
        │                │  └───┬───────────┬────────┘           │       │
        │                │      │ watcher.py│ sweeper.py          │       │
        │                │   ┌──▼───┐    ┌──▼─────┐               │       │
        │                │   │evm.py│    │evm.py  │  sign+send ───┘       │
        │                │   │ton.py│    │(sweep) │                       │
        │                │   └──┬───┘    └──┬─────┘                       │
        └────────────────┘      │           │                            │
                        keyless JSON-RPC     │ auto-forward               │
                       ┌────────▼────────┐   │              ┌─────────────▼──┐
                       │ BSC / ETH /     │◄──┘  sweep tokens │  your COLD      │
                       │ Polygon / TON   │─────────────────►│  main wallet    │
                       └─────────────────┘                  └─────────────────┘
```

| Component | File | Responsibility |
|---|---|---|
| **Config** | `optimus_gateway/config.py` | 12-factor `Config` class; every knob is an `OPG_*` env var with a safe default. Enforces the secrets policy (xpub in env OK; sweep xprv only from a `0600` file; cold seed never present). `Config.summary()` powers `/health`. |
| **Chain registry** | `optimus_gateway/chains.py` | The single source of truth for every network: `CHAINS` dict (contracts, `decimals`, keyless `rpcs`, block-scan tuning, cursor key). Money helpers `cents_divisor` / `to_cents` / `to_raw`, plus `is_evm`, `chain_id`, `native_coin`, `to_topic_address`, and the ERC-20 `EVM_TRANSFER_TOPIC`. |
| **DB / ledger** | `optimus_gateway/db.py` | SQLite (WAL). Orders, the anti-replay `payment_reference_registry`, HD index allocation (`address_counter`), settings/cursors, and the `webhook_queue`. All money is integer cents; all crediting is idempotent and transactional (`BEGIN IMMEDIATE`). |
| **HD wallet** | `optimus_gateway/hdwallet.py` | BIP32/44 derivation (`m/44'/60'/0'/0/i`, `Bip44Coins.ETHEREUM`). `address_from_xpub` (watch-only), `child_privkey`/`address_of_privkey` (sweep signing), `validate_xpub` (rejects private keys), `generate_dedicated_wallet`, and locked-file `load/save_sweep_xprv`. |
| **EVM RPC** | `optimus_gateway/evm.py` | Keyless JSON-RPC over rotated public endpoints. `get_logs_transfers` (the money-in scan; re-verifies every log), balances, nonce, gas price, and transaction sign/broadcast for sweeps. |
| **TON** | `optimus_gateway/ton.py` | toncenter v3 polling of incoming USDT-jetton transfers; address conversion; extracts the per-order text comment (memo). |
| **Watcher** | `optimus_gateway/watcher.py` | The money-in engine. Per-chain crash-safe block scan (EVM) or memo poll (TON); credits owning orders idempotently; fires `on_paid(order)` when an order flips to PAID. |
| **Sweeper** | `optimus_gateway/sweeper.py` | The money-out engine (optional). Gas-tank management, auto-forward to the cold wallet, and wrong-network recovery. `gas_tank_status`, `recover_wrongnet`, `sweep_once`. |
| **Gateway API** | `optimus_gateway/gateway.py` | The high-level facade your app calls: `create_payment` / `get_payment` returning a stable public order dict. Chooses the attribution mode automatically. |
| **Security** | `optimus_gateway/security.py` | `normalize_reference` (the replay key), HMAC-SHA256 `sign_params`/`verify_params` (merchant request auth) and `sign_webhook`, constant-time compare. |
| **Webhook** | `optimus_gateway/webhook.py` | Signed, queued, retried server-to-server callbacks. `build_payload`, `on_paid` (enqueue), `deliver_due` (exponential backoff up to `WEBHOOK_MAX_RETRIES`). |
| **Server** | `server/app.py`, `server/workers.py` | FastAPI REST + hosted checkout page/QR/poll, and three daemon worker loops (watcher / sweeper / webhook). |
| **Entrypoint** | `run.py` | `serve` (API + workers), `newwallet`, `checkxpub`, `recover`, `tanks`. |

The `optimus_gateway` package is usable as a **library** (`from optimus_gateway
import init, create_payment, get_payment`) or as a **service** (`python run.py`).
`init()` just calls `db.init_db()` (idempotent).

---

## 2. The payment lifecycle, end to end

### 2.1 Create → derive address

`POST /api/v1/order/create` → `server/app.py:create_order` authenticates the
merchant (`_require_merchant`) and calls `gateway.create_payment(method, amount,
…)`.

`create_payment` (`optimus_gateway/gateway.py`) validates the method is known and
enabled, then picks the attribution mode:

- **TON** (`usdt_ton`): `pay_address = config.TON_RECEIVE_ADDRESS`, and a fresh
  per-order memo `pay_memo = _new_memo()` (`"OPG" + 13× Crockford base32`, ~65 bits).
- **EVM + `OPG_GATEWAY_XPUB`** (recommended): allocate the next HD index from a
  **single global counter** — `db.next_address_index(conn, "_evm")` inside a
  `BEGIN IMMEDIATE` transaction — then derive
  `pay_address = hdwallet.address_from_xpub(config.GATEWAY_XPUB, address_index)`.
  A shared index across BSC/ETH/Polygon guarantees per-order addresses never
  collide across chains.
- **EVM + `OPG_SHARED_RECEIVE_ADDRESS`** (fallback): `pay_address` is the shared
  address and `amount_match=True`.
- Otherwise: `RuntimeError` telling you to configure one of the two.

It then calls `db.create_order(...)`, which reserves the order in a `BEGIN
IMMEDIATE` transaction:

- `base_cents = round(quote_amount * 100)` (must be `> 0`).
- If `merchant_order_id` was supplied and already exists, the existing order is
  returned unchanged — **idempotent order creation** (backed by the unique index
  `idx_orders_merchant`).
- In amount-match mode, `_unique_amount_cents` bumps `expected_cents` up by 1 cent
  at a time until it is not already "taken" by another active/cooldown order on
  that method, so the on-chain **amount uniquely identifies the order**.
- `reservation_expires_at = now + RESERVATION_TTL_MINUTES` (default 40).

`create_payment` returns `_public_order(order)`: `trade_id`, `pay_address`,
`pay_amount` (`"%.2f"` of cents), `pay_amount_cents`, `pay_memo`, `payment_uri`,
`checkout_url` (`{BASE_URL}/pay/{trade_id}`), `explorer`, `status`, `expires_at`,
etc. Show the address + amount (+ QR from `/pay/{trade}/qr.png`) or redirect to
`checkout_url`.

### 2.2 Watch (getLogs) → credit idempotently

The watcher loop (`server/workers.py:_watch_tick`) calls
`watcher.scan_all(on_paid=webhook.on_paid)` every `WATCH_POLL_SECONDS`. For each
enabled method:

- **EVM** → `scan_evm` (see §5 for the crash-safe cursor). It resolves the
  addresses to watch (per-order addresses via `db.active_order_addresses`, or the
  single shared address), then for each watched token calls
  `evm.get_logs_transfers(eps, contract, addr_chunk, start, end)` in `max_span`
  windows. Each returned transfer is converted to cents (`raw // divisor`), and:
  - per-order mode → `db.credit_by_address(method, to, cents, txid)` (matched by
    address alone).
  - amount-match mode → only **whole-cent** transfers (`raw % divisor == 0`) →
    `db.credit_by_amount(method, cents, txid)`.
- **TON** → `scan_ton` polls `ton.fetch_incoming`, and for each transfer with a
  comment calls `db.credit_by_memo(method, comment, cents, txid)`.

All three `credit_by_*` paths funnel into `db._apply_credit` inside a `BEGIN
IMMEDIATE` transaction, which:

1. **Burns the txid** into `payment_reference_registry` via `_claim_reference`
   *before* touching any balance. If the reference was already used → returns
   `{"status": "already_used"}` and no credit happens (see §6).
2. Accumulates: `received_cents += cents`, appends the txid to `tx_hashes`.
3. Flips to `paid` (setting `paid_at`) **only** when `received_cents >= expected_cents`
   and the order is still `pending`. Records `overpaid_cents = max(0, total −
   expected)`. Returns `paid` / `partial` / `topup` (see §7).

When a credit returns `paid`, `scan_evm`/`scan_ton` calls `_fire_paid(order_id,
on_paid)`, which loads the order and invokes the callback.

### 2.3 Webhook

`webhook.on_paid(order)` enqueues a callback (`db.enqueue_webhook`) if the order
has a `notify_url`. The webhook loop (`_webhook_tick`, every 5 s) runs
`webhook.deliver_due`, which POSTs the signed JSON payload
(`build_payload` → HMAC-SHA256 `signature` field **and** `X-OPG-Signature`
header) to the merchant. Non-2xx / errors retry with exponential backoff
(`_backoff = min(3600, 30·2^attempts)`) up to `WEBHOOK_MAX_RETRIES` (default 6),
after which the row is marked `failed`. The merchant can always fall back to
polling `GET /api/v1/order/{trade_id}` or `/pay/{trade_id}/status`.

### 2.4 Optional sweep

If `OPG_AUTO_SWEEP=true` and the sweep xprv is present, the sweeper loop
(`_sweep_tick`, every `WRONGNET_POLL_SECONDS`, default 900 s) runs
`sweeper.recover_wrongnet(credit=True)`, which forwards funds off the per-order
addresses to your cold wallet and simultaneously performs wrong-network recovery
(see §8). Without auto-sweep the gateway is fully **watch-only**: it credits and
notifies, and you sweep with your own offline seed.

---

## 3. The two attribution modes

The gateway needs to answer "which order does this on-chain payment belong to?".
There are two EVM strategies (plus TON memo, which is conceptually the same as
per-order addressing but using a comment instead of an address).

```
 (A) PER-ORDER ADDRESS  (OPG_GATEWAY_XPUB set)         RECOMMENDED
 ─────────────────────────────────────────────────────────────────
   order #101 ──► address = xpub / index 5   ┐
   order #102 ──► address = xpub / index 6   ├─ each order = its own
   order #103 ──► address = xpub / index 7   ┘  globally-unique address
                                                (same index works on BSC,
   watcher: eth_getLogs Transfer(TO in {addr5,addr6,addr7})  ETH, Polygon)
   credit:  db.credit_by_address(method, to_addr, cents, txid)
            └─ matched by ADDRESS ALONE ─► exact order, any EVM chain
   pro: exact reconciliation, any amount, wrong-network recovery, privacy
   con: needs an xpub; sweeping needs the gas tank


 (B) UNIQUE-AMOUNT / SHARED ADDRESS  (OPG_SHARED_RECEIVE_ADDRESS set)
 ─────────────────────────────────────────────────────────────────
   order #201 wants $25.00 ──► reserve 2500 cents  ┐  all to ONE
   order #202 wants $25.00 ──► reserve 2501 cents  ├─ shared address;
   order #203 wants $25.00 ──► reserve 2502 cents  ┘  the AMOUNT is the id
                                       (_unique_amount_cents bumps by 1¢)
   watcher: eth_getLogs Transfer(TO = shared address)
   credit:  db.credit_by_amount(method, cents, txid)  [whole-cent only]
            └─ matched by exact expected_cents + method + pending
   pro: no xpub, one static address
   con: amount collisions if concurrency is very high; anti-front-run
        relies on the 24h cooldown + whole-cent match (see SECURITY.md)


 (C) TON MEMO  (usdt_ton)
 ─────────────────────────────────────────────────────────────────
   every order ──► one shared TON address + unique text comment "OPG…"
   credit: db.credit_by_memo(method, comment, cents, txid)
```

Mode selection is automatic in `gateway.create_payment`: TON → memo; EVM with an
xpub → per-order address; EVM with only a shared address → amount-match.
`Config.summary()` exposes `per_order_address_mode` / `amount_match_mode`.

Why per-order addressing is preferred: it needs no amount juggling, tolerates any
number of concurrent orders, gives clean 1:1 reconciliation and better privacy,
and — because every EVM chain shares one address space — it is what makes
**wrong-network recovery** possible (§8).

---

## 4. The exact-cents money model

**No floats ever touch the ledger.** Every amount is an integer number of cents
(1 USDT = 100 cents). On-chain tokens use different decimals per chain, so the
registry stores the divisor implicitly:

```
cents = raw_token_units // 10 ** (decimals - 2)          # chains.cents_divisor / to_cents
raw   = cents           *  10 ** (decimals - 2)          # chains.to_raw
```

From `optimus_gateway/chains.py`:

| Method | Chain | `decimals` | `cents_divisor = 10**(decimals-2)` | 1 cent in raw units |
|---|---|---|---|---|
| `usdt_bep20` | BSC (56) | 18 | `10**16` | 10,000,000,000,000,000 |
| `usdt_erc20` | Ethereum (1) | 6 | `10**4` | 10,000 |
| `usdt_polygon` | Polygon (137) | 6 | `10**4` | 10,000 |
| `usdt_ton` | TON | 6 | `10**4` | 10,000 |

Integer floor division means sub-cent dust is simply ignored (it never inflates a
credit). The **whole-cent** predicate `raw % divisor == 0` in `scan_evm` is what
lets amount-match mode trust "this transfer is exactly N cents"; per-order mode
does not require it because the address already identifies the order.

`quote_amount` (the fiat the merchant quoted) is stored for display only;
`expected_cents = round(quote_amount * 100)` (1:1 for USD) is the number the
engine actually reconciles against. All public/webhook amounts are rendered as
`f"{cents/100:.2f}"` at the edge — the float appears only in presentation.

---

## 5. The crash-safe block cursor (EVM)

`scan_evm` (`optimus_gateway/watcher.py`) is designed so that **a crash, a slow
node, or a lying RPC can never skip a payment**. The worst case is a re-scan,
which idempotency (§6) makes harmless.

```
latest        = evm.block_number(eps)              # tip
confirmed_to  = latest - MIN_CONFIRMATIONS         # never scan unconfirmed blocks
last          = settings[cursor_key]               # persisted per chain
from_block    = max(1, last + 1 - RESCAN_OVERLAP)  # deliberate re-scan overlap
scan_to       = min(confirmed_to, from_block + MAX_CATCHUP_BLOCKS - 1)  # bounded catch-up
```

Invariants:

- **Only confirmed blocks are scanned.** `confirmed_to = latest −
  MIN_CONFIRMATIONS` (default 3, clamped 1–50). A re-org shallower than the
  confirmation depth can't affect a credited block.
- **The cursor advances only over blocks actually scanned.** `db.set_setting(cursor_key,
  scan_to)` runs only if `ok_all` is `True` — i.e. every `get_logs_transfers`
  call in the sweep of `[from_block, scan_to]` succeeded.
- **Never advance on an RPC error.** `evm.get_logs_transfers` returns
  `(transfers, ok)`; on any RPC failure `ok=False`, which sets `ok_all=False`,
  breaks the loop, and leaves the cursor where it was. Next tick simply retries
  the same window.
- **`RESCAN_OVERLAP`** (default 24 blocks) re-scans a small tail every tick so a
  transfer straddling the previous boundary (or arriving during a brief re-org)
  is never missed. Re-scanning already-credited txids is a no-op thanks to the
  reference registry.
- **Bounded catch-up.** After downtime the cursor walks forward at most
  `MAX_CATCHUP_BLOCKS` (default 1500) per tick, in `max_span` sub-windows per
  chain (BSC 80, ETH 500, Polygon 20), so it recovers gradually without asking a
  public node for an enormous range.
- **No-orders fast-forward.** In per-order mode, if there are currently no active
  addresses to watch, the cursor is still advanced to `scan_to` (there is nothing
  to find in those blocks), so the gateway doesn't perpetually re-scan history
  once it is caught up.

Each per-chain cursor lives in `settings` under `cursor_key`
(`bep20_watch_last_block`, `erc20_watch_last_block`, `polygon_watch_last_block`).
On a cold start (`last <= 0`) it begins at `confirmed_to − initial_lookback`.

---

## 6. Idempotency: burn the reference before you credit

The anti-replay guarantee lives in one table:

```sql
CREATE TABLE payment_reference_registry (
    normalized_reference TEXT PRIMARY KEY,   -- the lock
    original_reference   TEXT,
    reference_type       TEXT,               -- which chain/source it arrived on
    order_id             INTEGER,
    created_at           TEXT
);
```

`db._apply_credit` runs inside a `BEGIN IMMEDIATE` transaction and calls
`_claim_reference` **first**, before mutating `received_cents`:

```python
if txid and not _claim_reference(conn, txid, reference_type, oid):
    return {"status": "already_used", "order_id": oid}   # replay → refuse
```

`_claim_reference` normalizes the reference (`security.normalize_reference`:
uppercased, strips quotes/whitespace, keeps the longest digit-bearing token so
`0xABC…` and copy-paste noise collapse to one key) and `INSERT`s it. Because
`normalized_reference` is the **PRIMARY KEY**, a duplicate raises
`sqlite3.IntegrityError`, which is caught and returned as "already used". Since
the claim and the balance update are in the same transaction, either both commit
or neither does — a re-scan, a retry, or two workers racing can **never
double-credit**. This is why the re-scan overlap and unbounded retries in §5 are
safe.

References claimed:

- EVM: the on-chain `transactionHash`.
- TON: the transaction hash from toncenter.
- Wrong-network recovery: a synthetic `WRONGNET-<METHOD>-<address>-<contract>`
  key (§8) so repeated recovery ticks over the same balance don't re-credit.
- Binance verify (optional): the Pay order id or deposit txid, burned by the
  caller after a successful match.

---

## 7. Credit-not-consume: accumulation and overpay

Payments are **credited, not consumed**. `_apply_credit` never rejects an
underpayment — it accumulates:

```python
new_total = received_cents + cents
# flip to PAID only once the running total covers expected
if pending and new_total >= expected_cents:
    status = paid
elif pending:
    status = partial     # still short — waiting for more
else:
    status = topup       # already paid; this is an extra/late deposit
```

Consequences:

- **Underpayment** → order stays `pending`, `received_cents` reflects the partial
  total, and the checkout page shows `received / expected`. The buyer can send the
  remainder (any number of transfers) and the order flips to `paid` once covered —
  each transfer is a separate, idempotent credit.
- **Overpayment** → the order flips to `paid` and `overpaid_cents = max(0,
  total − expected)` is reported so the merchant can refund or credit a wallet.
- **Late top-ups** → a deposit arriving after `paid` returns `topup` (the order
  isn't re-paid, but the funds are recorded and, in per-order mode, still swept).
  This is why watched/creditable orders include recently-paid ones within
  `AMOUNT_COOLDOWN_MINUTES` (default 1440 = 24 h), both in
  `db.active_order_addresses` and in `_unique_amount_cents`'s "taken" set.

---

## 8. Sweeper: gas tank + wrong-network recovery

The sweeper (`optimus_gateway/sweeper.py`) is the optional money-out engine. It
requires the dedicated hot-wallet **xprv** (`hdwallet.load_sweep_xprv` from the
`0600` file at `GATEWAY_SWEEP_KEY_PATH`); if absent, everything above still works
in pure watch-only mode.

### Gas tank (index 0, same address on every EVM chain)

Per-order addresses receive tokens but hold no native coin to pay the gas needed
to move those tokens. The **gas tank** is child index 0 of the dedicated wallet —
`_tank_addr = address_of_privkey(child_privkey(xprv, 0))`. Because every EVM chain
shares one secp256k1 address space, **index 0 is the same address on BSC,
Ethereum and Polygon**; you fund it with a little BNB / ETH / POL respectively
(each chain needs its own native balance — the BNB tank can't pay Ethereum gas).

`_gas_up_and_sweep` implements just-in-time gassing: it computes the native cost
of the pending token transfers, and if the per-order address is short, sends a
top-up from the tank (`evm.send_native`), waits up to ~60 s for it to confirm,
then signs and broadcasts the token sweep(s) (`evm.send_token`) to
`OPG_SWEEP_DESTINATION`. Per-chain gas limits/floors/caps live in the `GAS` table;
`gas_tank_status()` (and `python run.py tanks`) reports the tank balance on each
chain, with `GAS_ALERT_THRESHOLD` as the low-fuel line.

### Wrong-network recovery (one address space ⇒ one key)

Because a per-order address is derived once but is valid on **all** EVM chains, a
buyer who pays on the wrong network (say USDC on Ethereum when you quoted BEP20)
still sends to an address you control. `recover_wrongnet(credit=True)` turns that
mistake into a normal payment:

1. `db.all_evm_order_addresses()` returns every distinct per-order EVM address
   (any status) with its HD index.
2. For **each EVM method** and each address, it reads on-chain token balances
   (`_token_balances`). If any USDT/USDC is sitting there:
   - **Safety re-derivation:** it recomputes the address from `child_privkey(xprv,
     index)` and skips unless it matches — proof the gateway actually controls the
     key before it ever signs.
   - **Credit idempotently:** `db.credit_by_address(method, addr, cents, synth)`,
     where `synth = "WRONGNET-<METHOD>-<addr>-<contract>"`. `credit_by_address`
     matches by **address alone**, so the *correct order* is credited even though
     the money arrived on the "wrong" chain. The synthetic reference is burned in
     the registry so repeated ticks don't re-credit.
   - **Sweep home:** `_gas_up_and_sweep` forwards the balance to the cold wallet.

`sweep_once()` is `recover_wrongnet(credit=False)` — in per-order mode, recovery
*is* the auto-sweep, so the normal sweep and the wrong-network path are the same
code. The worker loop calls it with `credit=True` so a late/wrong-network deposit
both credits the order and lands in cold storage in one pass.

Your **cold main wallet seed is never on the server.** The sweeper only ever
*sends to* `SWEEP_DESTINATION`; it cannot spend it.

---

## 9. Data model summary (`db.py`)

| Table | Purpose | Key columns / invariants |
|---|---|---|
| `orders` | one row per payment | `trade_id` (public id), `merchant_order_id` (unique when set → idempotent create), `method`, `expected_cents`, `received_cents`, `address_index` + `pay_address` (per-order mode), `pay_memo` (TON), `status` (`pending`/`paid`/`expired`), `tx_hashes`, `reservation_expires_at`, `paid_at`. Indexed on `(method,status)`, `(method,expected_cents,status)`, `pay_address`. |
| `payment_reference_registry` | anti-replay lock | `normalized_reference` **PRIMARY KEY** — burned before crediting. |
| `address_counter` | HD index allocation | `next_index` starts at 1 (index 0 = main/gas tank); EVM uses the shared `_evm` key. |
| `settings` | cursors + RPC overrides | per-chain block cursor (`*_watch_last_block`), optional RPC override (`*_gateway_rpc`). |
| `webhook_queue` | outbound callbacks | `status` (`pending`/`delivered`/`failed`), `attempts`, `next_attempt_at`, `last_error`. |

All writes that move money use `BEGIN IMMEDIATE` so two worker threads (or the API
plus a worker) can't race; SQLite runs in WAL with a 30 s busy timeout.

---

## 10. Request/response surface (`server/app.py`)

| Endpoint | Who | Notes |
|---|---|---|
| `POST /api/v1/order/create` | merchant (signed) | body → `gateway.create_payment`; returns `{status_code, data: <public order>}`. Auth via `_require_merchant` (api_key + HMAC signature); if `OPG_MERCHANT_API_KEY` is empty, auth is disabled (single-tenant/trusted network). |
| `GET /api/v1/order/{trade_id}` | merchant | current public order dict. |
| `GET /pay/{trade_id}` | payer | hosted checkout HTML (address, amount, live QR, JS poll). |
| `GET /pay/{trade_id}/status` | payer JS | `{status, received_cents, expected_cents}`. |
| `GET /pay/{trade_id}/qr.png` | payer | QR PNG (via `segno`). |
| `GET /health` | ops | `Config.summary()` — non-secret config snapshot. |

On startup the app runs `opg.init()` (DB) and `workers.start_background()` (the
three daemon loops).

---

## 11. vs epusdt

This project adopts [epusdt](https://github.com/GMWalletApp/epusdt)'s clean,
proven **merchant-facing model** — `create-order → address + exact amount → signed
server-to-server webhook`, all on plain SQLite with no Redis/MySQL — and keeps
that surface deliberately familiar. What we add on top:

| | epusdt | Optimus Payment Gateway |
|---|---|---|
| Signing | MD5 over sorted params | **HMAC-SHA256** (`security.sign_params`) — authentication, not just integrity |
| Chains | TRON USDT | **BSC / Ethereum / Polygon** (USDT + USDC) **+ TON** |
| Attribution | shared address, amount match | **per-order xpub addresses** (watch-only HD) *or* amount-match |
| Custody / keys | wallet-managed | **watch-only xpub** (zero private keys) or dedicated hot wallet with a `0600` xprv |
| Sweeping | — | **gas-tank auto-sweep** to a cold main wallet (JIT gassing) |
| Mistakes | — | **wrong-network recovery** (one EVM address space, one key) |
| TON | — | **TON memo** routing |
| Second source | — | optional **Binance verify** (read-only API key) |

The result: epusdt's ergonomics with a materially more capable, non-custodial
chain engine. See [`SECURITY.md`](SECURITY.md) for the key model and threat
analysis.
