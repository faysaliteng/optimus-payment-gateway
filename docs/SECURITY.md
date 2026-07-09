# Security

This software moves real money on public blockchains. This document describes the
key model, the idempotency guarantee, the threat model with concrete mitigations,
a hardening checklist, and how to report a vulnerability. Everything maps to real
code in `optimus_gateway/`.

Read [`ARCHITECTURE.md`](ARCHITECTURE.md) first for the component map and lifecycle.

---

## 1. Key model — where every secret lives (and doesn't)

The gateway is designed so that, in the recommended configuration, **the server
holds no spendable key at all.** There are three distinct key materials, kept
strictly separated:

| Key | What it can do | Where it lives | In git / env / logs / DB? |
|---|---|---|---|
| **Receiving XPUB** (`OPG_GATEWAY_XPUB`) | *Watch only.* Derive per-order receiving addresses `m/44'/60'/0'/0/i`. **Cannot spend.** | env / `.env` | env is fine — it is public-safe by construction |
| **Sweep XPRV** (dedicated hot wallet) | Move funds *off* the per-order/gas-tank addresses (auto-sweep). Only ever **sends to** the cold destination. | a `0600` file at `OPG_SWEEP_KEY_PATH` (default `private/gateway_sweep/account.xprv`) | **never** — not in git, not in an env var, not logged, not in the DB |
| **Cold main wallet seed** (`OPG_SWEEP_DESTINATION` is only its *address*) | Spend your actual funds. | **offline**, off this machine entirely | **never on the server** — the sweeper only knows the destination *address* |

### 1.1 Watch-only by default (zero private keys)

In per-order mode the server is given only `OPG_GATEWAY_XPUB`, a **watch-only
account xpub** (`m/44'/60'/0'/0`). `hdwallet.address_from_xpub` derives a fresh
child address per order; there is no private key anywhere in the process, so an
attacker who fully compromises the box **cannot move a single token** — they can
only observe addresses. You sweep with your own offline seed on your own schedule.

`hdwallet.validate_xpub` actively defends this posture: it **rejects** anything
starting with `xprv`/`yprv`/`zprv`/`tprv` ("that is a PRIVATE key — paste the
xPUB instead"), so a private key can never be accidentally stored in the xpub
field. `python run.py checkxpub <xpub>` runs this check and previews addresses.

### 1.2 Dedicated hot wallet for sweeping (opt-in)

Auto-sweep needs to *sign* transactions, so it needs a private key — but never
*your* private key. `python run.py newwallet` (`hdwallet.generate_dedicated_wallet`)
mints a brand-new, isolated BIP39 wallet used **only** by the gateway:

- its **account xprv** is written to a `0600` file (`hdwallet.save_sweep_xprv`,
  which also `chmod 700`s the directory) and read back with
  `load_sweep_xprv` — **never** an env var, never the DB, never a log line;
- its **account xpub** becomes `OPG_GATEWAY_XPUB` (the receiving key);
- **index 0** is the gas tank; **indices 1,2,3…** are per-order receiving
  addresses;
- the **mnemonic** is shown once for you to back up offline, then discarded.

Blast radius if this hot wallet leaks: only funds *currently sitting on gateway
addresses* (per-order balances not yet swept + the small gas-tank float). It
**cannot** touch your cold wallet. Mitigation: keep the tank modestly funded, keep
sweeps frequent, and rotate the wallet if the file is exposed (see §3).

### 1.3 The cold wallet seed is never here

`OPG_SWEEP_DESTINATION` is only an **address**. The sweeper (`sweeper.py`) exclusively
*sends to* it (`evm.send_token(... dest ...)`); it has no key for it and cannot
spend from it. Your cold seed stays offline, full stop.

---

## 2. The idempotency guarantee (no double-credit, ever)

The single most important money-safety property: **a payment reference is burned
into `payment_reference_registry` before any balance is credited, in the same
transaction.**

- `payment_reference_registry.normalized_reference` is a **PRIMARY KEY**.
- `db._apply_credit` runs in `BEGIN IMMEDIATE` and calls `_claim_reference`
  *before* updating `received_cents`. A duplicate insert raises
  `sqlite3.IntegrityError`, caught and returned as `already_used` → **no credit**.
- Because the claim and the credit are atomic, a re-scan, a retried webhook, two
  racing worker threads, or a replayed txid can never add balance twice.

`security.normalize_reference` canonicalises references (uppercase, strip
quotes/whitespace, keep the longest digit-bearing token) so `0xABC…` and
copy-paste noise collapse to the same key. This is what makes the watcher's
deliberate re-scan overlap (`RESCAN_OVERLAP`) and unbounded RPC retries safe.

---

## 3. Threat model & mitigations

### 3.1 A malicious or compromised RPC lies to us

**Threat:** a public RPC endpoint returns forged `eth_getLogs` results (fake
Transfer to an order's address) to trigger a false credit, or omits real logs.

**Mitigations:**
- Every log is **re-verified in-process** in `evm.get_logs_transfers`: the log's
  `address` must equal the token contract, `topics[0]` must equal the canonical
  ERC-20 `EVM_TRANSFER_TOPIC`, and the destination topic must be one we actually
  asked about. A node can't make us credit a transfer of the wrong token or to the
  wrong address.
- A forged credit still cannot become spendable money out of thin air: it would
  have to correspond to tokens that actually arrived at an address **you** control
  (per-order mode) — the attacker gains nothing by faking a log for an address
  that holds no real balance.
- **Never advance the cursor on error:** `get_logs_transfers` returns
  `(…, ok=False)` on any RPC failure; `scan_evm` then does *not* persist the block
  cursor, so a flaky/hostile node causes a retry, never a skipped payment (see
  ARCHITECTURE §5).
- Endpoints are **rotated** (`chains.py` `rpcs`, plus an optional per-chain
  override in `settings`), so one bad node is routed around.
- **Confirmations:** `MIN_CONFIRMATIONS` (default 3) means only settled blocks are
  scanned, defeating shallow re-org tricks.

### 3.2 Replay / double-credit

**Threat:** the same txid (or webhook, or scan window) is processed twice.

**Mitigation:** the reference registry PRIMARY KEY (§2). Replays return
`already_used`. This covers on-chain re-scans, wrong-network recovery re-runs
(synthetic `WRONGNET-…` key), TON re-polls, and Binance re-verification.

### 3.3 Front-running / guessing an amount (amount-match mode)

**Threat:** in shared-address amount-match mode, an attacker watches the mempool,
sees the exact amount another buyer is about to pay, and races a transfer of that
same amount to claim the credit — or brute-forces amounts.

**Mitigations:**
- **Whole-cent match only:** `scan_evm` credits amount-match transfers only when
  `raw % divisor == 0`, and matches on the *exact* `expected_cents` for that
  method + `pending` status (`db.credit_by_amount`).
- **Unique amount per active order:** `db._unique_amount_cents` guarantees no two
  concurrently-active orders on a method share a cents value.
- **24-hour cooldown:** an amount stays reserved for `AMOUNT_COOLDOWN_MINUTES`
  (default 1440) after payment, so it can't be immediately recycled/confused with
  a late payment.
- **Prefer per-order addresses.** The clean fix is `OPG_GATEWAY_XPUB` mode: each
  order gets its own globally-unique address and is matched by **address, not
  amount**, which removes the front-running surface entirely. Amount-match is the
  fallback for when you insist on a single static address.

### 3.4 Leaked receiving xpub

**Threat:** `OPG_GATEWAY_XPUB` is exposed.

**Impact:** an xpub reveals **addresses and their balances only** — it grants **no
spending ability**. The practical downside is *privacy* (an observer can link your
order addresses). Rotate to a fresh account xpub if you care about unlinkability;
funds are never at risk from an xpub leak.

### 3.5 Leaked sweep xprv

**Threat:** the `0600` sweep-key file is read by an attacker.

**Impact:** they can move **gateway hot funds only** — balances currently on
per-order addresses plus the gas-tank float. They **cannot** touch your cold
wallet (its seed isn't here). **Response:** treat it as a hot-wallet compromise —
generate a new dedicated wallet (`python run.py newwallet`), point
`OPG_GATEWAY_XPUB` at the new account xpub, move any remaining balances, and keep
the new key file `0600`. Keep the tank small so the exposed amount is bounded.

### 3.6 Forged create-order or spoofed webhook

**Threat:** someone calls `POST /api/v1/order/create` without authorization, or a
merchant is tricked by a fake "payment.completed" callback.

**Mitigations:**
- **Inbound auth:** `server/app.py:_require_merchant` requires the correct
  `api_key` **and** a valid HMAC-SHA256 `signature` over the sorted request params
  (`security.verify_params`) using `OPG_MERCHANT_API_SECRET`. Comparison is
  constant-time (`hmac.compare_digest`).
- **Outbound integrity:** every webhook carries an HMAC-SHA256 `signature` field
  **and** an `X-OPG-Signature` header (`webhook.build_payload` /
  `security.sign_webhook`). Merchants **must** recompute the signature with the
  shared secret before trusting a callback — never act on an unsigned/unverified
  webhook. (Also: treat the webhook as a hint and confirm via
  `GET /api/v1/order/{trade_id}` for high-value orders.)
- If `OPG_MERCHANT_API_KEY` is empty, inbound auth is **disabled** — only do this
  on a trusted, non-public network (single-tenant behind a firewall).

### 3.7 Binance verification abuse (optional feature)

**Threat:** tampering with the optional Binance cross-check.

**Mitigations:** `binance.py` uses a **read-only** personal API key (never
withdrawal permission), signs every request with HMAC-SHA256 (Binance's standard
signed-endpoint scheme), requires a matching reference **and** amount (within
`BINANCE_AMOUNT_TOLERANCE`) **and** a success status, and the caller burns the
reference in the registry so it can't be replayed.

### 3.8 Database / host compromise

**Threat:** attacker reads or writes `optimus_gateway.db` or the host.

**Impact & mitigations:** the DB contains order metadata and cursors but **no
private keys** (watch-only mode) — worst case is data disclosure, not fund theft.
Writing the DB could mark orders paid, but cannot conjure on-chain funds; keep DB
file permissions tight, run the process as an unprivileged user, and back up the
DB (§4). In dedicated-wallet mode, the value at risk is still only the hot-wallet
float, protected by the `0600` key file that is separate from the DB.

### 3.9 A fake / scam token is sent to a gateway address

**Threat:** a scammer deploys a token they **name** "USDT" / "USDC" / "BSC-USD" at a
contract of their own and sends it to one of your gateway addresses, hoping it is
mistaken for a real stablecoin payment and credited.

**Mitigations:**
- **Contract allowlist, not names.** The gateway only ever scans, credits, and sweeps
  the **exact real token contracts** in the `chains.py` `CHAINS` registry — surfaced as
  `REAL_STABLECOIN_CONTRACTS` and `is_real_stablecoin()`. Every `eth_getLogs` scan is
  filtered **by contract address** (`evm.get_logs_transfers`, `address: contract`) and
  re-verified in-process, so a token at any *other* contract is **never seen**: its
  transfers are not scanned, not credited, and not swept. The token's *name/symbol* is
  irrelevant — only the on-chain contract address decides whether it is money.
- **Defense-in-depth guard.** `watcher._watched_tokens` and `sweeper._token_balances`
  both pass every contract through `is_real_stablecoin()`, so even a stray bad entry in
  the registry could not cause a fake token to be watched or moved.
  `tests/test_fake_token.py` pins this: the full per-chain allowlist is verified, real
  contracts are accepted (case-insensitively), and real-world scam contracts observed in
  production (fake "USDT"/"BSC-USD") are rejected.
- **One place to add a coin.** To accept a new real stablecoin, add its verified
  contract to the registry — it is then covered everywhere at once. Anything not in the
  registry is, by construction, treated as a fake token and ignored.

---

## 4. Hardening checklist

Before mainnet, and on an ongoing basis:

- [ ] **HTTPS + reverse proxy.** Terminate TLS at nginx/Caddy in front of the app;
      never expose the raw `:8000` to the internet. Set `OPG_BASE_URL` to the
      public `https://` URL so `checkout_url` is correct.
- [ ] **Watch-only if you can.** Prefer `OPG_GATEWAY_XPUB` with no sweep key — zero
      spendable secrets on the server. Enable auto-sweep only if you need it.
- [ ] **Protect the sweep key.** `OPG_SWEEP_KEY_PATH` must be `0600`, owned by the
      service user, outside the repo, and excluded from backups that leave the
      host in cleartext. Never echo it, never put the xprv in an env var.
- [ ] **Keep the cold seed offline.** Only its *address* (`OPG_SWEEP_DESTINATION`)
      belongs on the server.
- [ ] **Strong `OPG_MERCHANT_API_SECRET`.** Long, random, unique. This secret both
      authenticates create-order and signs webhooks. Rotate if exposed. Set
      `OPG_MERCHANT_API_KEY` (don't run with auth disabled on a public host).
- [ ] **Verify webhook signatures** on the merchant side, every time; reconcile
      high-value orders against the query API.
- [ ] **Tune confirmations.** Keep `OPG_MIN_CONFIRMATIONS` at a sane depth for the
      value you accept (raise it for large amounts).
- [ ] **Fund the gas tank modestly.** Only enough native BNB/ETH/POL to cover
      sweeps — it's a hot balance and caps your worst-case loss if the sweep key
      leaks. Watch it with `python run.py tanks` / `GAS_ALERT_THRESHOLD`.
- [ ] **Restrict the admin dashboard.** Set a strong `OPG_ADMIN_PASSWORD` (empty =
      disabled) and keep admin routes off the public internet / behind allow-lists.
- [ ] **Lock down the DB & host.** Unprivileged service user, restrictive file
      permissions on the DB and key file, minimal open ports.
- [ ] **Back up** the SQLite DB (WAL-consistent copy) and your offline seed +
      dedicated mnemonic; store the mnemonic offline only.
- [ ] **Monitor.** Alert on `webhook_queue` rows stuck in `failed`, on a low gas
      tank, on RPC error spikes (cursor not advancing), and on unexpected
      `overpaid_cents` / `topup` events.
- [ ] **Test small first.** Run the whole flow with tiny amounts on each chain
      before accepting real volume, including a deliberate wrong-network payment.

---

## 5. Responsible disclosure

If you discover a security vulnerability, please report it **privately** to the
maintainer and allow a reasonable window for a fix before any public disclosure.
Do **not** open a public issue, PR, or social post that reveals exploit details,
and do not test against third parties' live deployments. Coordinated, good-faith
disclosure keeps real users' funds safe — thank you.
