# Litecoin (LTC) — the low-fee coin for small orders

Every gateway chain until now has been an **account-token** chain: you receive USDT/USDC
and the value is exactly what arrived. Litecoin is different in two ways that make it the
best rail for *small* orders, and this guide covers both — what it is, how to set it up
watch-only, the Electrum key gotcha it handles for you, how deposits are priced and
credited, the optional Phase-2 auto-sweep, and a ship-safe rollout checklist.

Litecoin support lives in `optimus_gateway/litecoin.py` — the UTXO sibling of
[`ton.py`](../optimus_gateway/ton.py). It reuses the same per-order-address model, the same
`db._apply_credit` idempotency, and the same watch-only-by-default key posture as the EVM
chains ([`SECURITY.md`](SECURITY.md)); only the address format, the block scanner, and the
signer are Litecoin-specific.

---

## 1. What it is

- **A UTXO per-order-address gateway.** Just like the EVM chains derive
  `m/44'/60'/0'/0/i` addresses from your xpub, Litecoin derives a fresh **native-segwit
  `ltc1…` (BIP84, P2WPKH)** address per order from a watch-only account key. The buyer
  pays that address; the watcher sees the deposit; the order is credited. No private key
  is needed to *detect* a payment.
- **It credits the USD value of the LTC that arrived**, not a token face value. LTC is
  volatile, so a deposit of `x` LTC is credited at `x × (live LTC/USD rate)` in whole
  cents — money is handled in integer cents everywhere ([`CHAINS.md`](CHAINS.md)), so a
  single rounding converts sats → USD cents and that is what the wallet is credited.
- **Address-based, not amount-match.** Under/overpayment is handled by *value* exactly
  like the EVM addressed mode: pay less → partial, pay the rest → credited, pay more →
  the extra is spendable balance. There is no exact-amount matching and therefore no
  front-running surface (see [`ADDRESS_POOL.md`](ADDRESS_POOL.md) §attribution).

## 2. Why Litecoin — the sweep-fee argument

The one honest cost of per-order addresses is **sweeping**: to collect many order
addresses into one cold wallet you pay a network fee per move.

| Rail | Typical sweep fee | Good for |
|---|---|---|
| Ethereum (`usdt_erc20`) | tens of cents – a few dollars | large orders only |
| BSC (`usdt_bep20`) | a fraction of a cent | most orders |
| **Litecoin (`ltc`)** | **~$0.0001** (sats at ~1–2 sat/vB) | ⭐ **tiny orders** |

A P2WPKH Litecoin sweep is a few hundred vBytes at 1–2 sat/vByte — effectively free. That
means you can collect a **$0.50 order** and still net almost all of it, which is
uneconomical on most chains once gas is subtracted. Litecoin fills the "small order" gap
that BEP20's [address pool](ADDRESS_POOL.md) fills a different way (by *batching* to
amortise EVM gas). If your catalogue has sub-dollar items, LTC is the cleanest answer.

## 3. Setup — watch-only (Phase 1)

Watch-only is the recommended posture: the server holds **no spending key**, only an
account **public** key, so a full host compromise still cannot move a coin.

### 3.1 Get a dedicated Litecoin wallet

Use a wallet you keep **only** for the gateway (so its seed is isolated and its receive
addresses aren't mixed with personal funds). Electrum-LTC, a hardware wallet, or any BIP84
Litecoin wallet works. Write the seed down offline — it is the only backup of received
funds and, in watch-only mode, the only key that can ever spend them.

### 3.2 Paste the account **zpub/xpub** (public key only)

Give the gateway the account **extended public key** — never the seed, never a `zprv`.
Store it as the `ltc_gateway_xpub` DB setting (or `OPG_LTC_XPUB`):

```bash
python -c "from optimus_gateway import db; db.set_setting('ltc_gateway_xpub', 'zpub6...')"
```

The derivation is fixed by the gateway to native segwit (`ltc1…`, BIP84 external chain),
so all you provide is the account key. `validate_ltc_xpub()` derives address 0 and
confirms it is an `ltc1…` address before you trust it.

### 3.3 The Electrum gotcha (handled automatically)

Electrum exports its master public key from *Wallet → Information*, but with two quirks
that would break a naïve parser:

1. it is a **depth-1** segwit key (Electrum's own `m/0'` account), not the depth-3
   `m/84'/2'/0'` a hardware wallet exports; and
2. it carries **Bitcoin `zpub` version bytes** (`0x04b24746`), not Litecoin's.

The parser normalises the 4 version bytes to the standard `xpub` version and derives
`change/index` **relative to whatever account node it is given** — so an Electrum depth-1
Bitcoin-versioned `zpub` and a BIP84 depth-3 Litecoin `Ltub`/`zpub` both parse and both
produce the *same* `ltc1…` addresses. The key material (chain code + pubkey) is never
altered; only the version prefix is standardised so the BIP32 library will accept it. You
paste whatever your wallet gives you; the address type is fixed by the gateway.

> Where to find it in Electrum: **Wallet → Information → Master Public Key.**

### 3.4 Enable the method + set the cold sweep destination

Add `ltc` to your enabled methods and set the cold address you'll (eventually) sweep to.
The destination must be a native-segwit `ltc1q…` (P2WPKH) address — e.g. a Trust Wallet or
hardware-wallet Litecoin receive address:

```bash
OPG_ENABLED_METHODS=usdt_bep20,ltc
# cold wallet (address only — its seed is NEVER on the server):
python -c "from optimus_gateway import db; db.set_setting('ltc_sweep_destination', 'ltc1q...')"
```

In watch-only mode the destination is not used until you turn on Phase-2 sweeping; setting
it now is harmless and lets you flip auto-sweep on later without touching anything else.

## 4. How deposits are detected and credited

- **Scanner: litecoinspace.org** — a mempool.space-style REST API for Litecoin, free and
  **no API key**. The watcher reads, per order address, the confirmed received sats, the
  per-`(txid, vout)` outputs paying the address, and (for sweeping) its live UTXOs.
- **Confirmed-only.** Only outputs whose status is `confirmed` are credited; unconfirmed
  mempool outputs are ignored until they bury in a block.
- **Idempotent per `(txid, vout)`.** Each output is claimed in
  `payment_reference_registry` **before** any balance is credited, in the same
  `BEGIN IMMEDIATE` transaction — exactly the guarantee described in
  [`SECURITY.md`](SECURITY.md) §2. A re-scan, a duplicated API response, or two racing
  watcher ticks can never double-credit the same output.
- **Accumulating credit.** Multiple deposits to one order address add up: each arrival
  credits its own USD value to the wallet and bumps the order's running total; the order
  flips to *paid* once the total covers what was owed. Overpay simply remains spendable
  balance.
- **Pricing.** `sats → USD cents` uses the live LTC/USD rate at the moment of crediting.
  If no rate is available the credit is deferred rather than credited at zero.

## 5. Phase-2 — optional auto-sweep (a hot key, bounded risk)

Watch-only can't sign, so collecting the per-order addresses into your cold wallet is
manual (you sweep with your offline seed). Phase 2 automates it by giving the gateway a
**dedicated hot signing key** — never your personal seed.

### 5.1 The signing key (locked file, never the DB)

Provide the **account extended private key** (`zprv`/`xprv`) that is the private twin of
the watch-only `zpub`. It is stored in a **`0600` file** (e.g.
`private/gateway_sweep/ltc_account.xprv`, pointed to by `OPG_LTC_SWEEP_KEY_PATH`) — the
same posture as the EVM sweep key: **never** in the DB, **never** an env var value,
**never** logged. `verify_sweep_key()` derives addresses 0/1/2/5 from the private key and
asserts they equal the addresses the watch-only `zpub` produced, so a mismatched key can
never sign for addresses the watcher didn't credit.

> **Getting the account `zprv`/`xprv` from Electrum:** open the console
> (*View → Show Console*) and run
> `wallet.keystore.get_master_private_key(password)`. Treat the result like a seed — paste
> it once into the locked file and nowhere else.

### 5.2 The BIP143 signer (no gas tank)

Litecoin uses Bitcoin's transaction format and **BIP143** segwit sighash verbatim — only
the address HRP (`ltc`) and network differ. The signer builds a native-segwit P2WPKH
spend, is validated byte-for-byte against the official BIP143 P2WPKH test vector before it
ever touches real coins, and produces RFC6979-deterministic, low-S ECDSA signatures via
libsecp256k1 (`coincurve`).

**The fee is paid in LTC out of the swept amount itself — there is no gas tank.** Unlike
the EVM chains (where a native-coin float pays gas), a UTXO sweep just deducts the tiny
miner fee from the consolidated inputs and sends the remainder. Fee rate comes from
litecoinspace's recommended sat/vByte (floor 1, fallback ~2); a dust floor (294 sats)
guards against sending a sub-dust output. An optional `ltc_sweep_min_usd` threshold holds
small balances until they're worth consolidating.

### 5.3 What a sweep actually does

Sweep candidates are chosen from **live on-chain UTXOs**, not a permanent "swept" flag: an
address whose sweep already confirmed returns no UTXOs (skipped), while a dropped sweep, a
payment that was unconfirmed at the last sweep, or a fresh deposit to an already-swept
address all reappear as UTXOs and are re-swept. Every candidate address is re-derived from
the hot key and its P2WPKH address must equal the stored `pay_address` before it is ever
signed for. The confirmed UTXOs are consolidated into **one** signed transaction to the
cold destination, broadcast via litecoinspace `POST /tx` (which must return a 64-hex txid,
or the deposit is left un-swept rather than wrongly marked collected), and the outbound
"forward to cold" move is logged so it's visible in the on-chain admin log (the watcher
only scans *incoming*).

## 6. Rollout checklist (ship disabled → one small real deposit → enable)

1. **Ship disabled.** Deploy with `ltc` **not** in `OPG_ENABLED_METHODS` (or the gateway
   toggle off). The module is inert until enabled — importing it does nothing.
2. **Configure watch-only.** Set `ltc_gateway_xpub` and run `validate_ltc_xpub()` /
   `checkxpub`-style preview; confirm address 0 is an `ltc1…` you recognise from your
   wallet.
3. **One small real deposit.** Enable `ltc`, create a tiny real order, send a few cents of
   LTC, and watch it credit at the live USD rate. This proves the derivation, the
   litecoinspace watcher, the confirmation gate, and the USD pricing end-to-end.
4. **Go live for receiving.** Leave it watch-only; sweep manually with your offline seed
   whenever you like. This is a complete, safe deployment.
5. **(Optional) Enable Phase-2 auto-sweep.** Only if you want hands-off collection: place
   the account `zprv`/`xprv` in the `0600` file, run `verify_sweep_key()` (it must match
   your `zpub`), set the destination + `ltc_sweep_min_usd`, then turn auto-sweep on. Test
   with one small forced sweep before relying on it.

## 7. Security note — watch-only vs hot key

| | **Watch-only (Phase 1)** | **Auto-sweep (Phase 2)** |
|---|---|---|
| Key on the server | account **zpub** only — cannot spend | dedicated account **zprv** in a `0600` file |
| If the host is fully compromised | attacker sees addresses/balances, **moves nothing** | attacker can move **only funds currently on gateway addresses** |
| Your cold wallet's seed | never on the server | never on the server (sweeper only *sends* to its address) |
| Sweeping | manual, with your offline seed | automatic, in-LTC fee, no gas tank |

The blast radius of the Phase-2 hot key is bounded to *un-swept deposit balances* — it
cannot touch the cold wallet, whose seed is off-box. Keep sweeps frequent (or the
`ltc_sweep_min_usd` threshold low) so the at-risk float stays small, and rotate the hot
wallet if the key file is ever exposed. When in doubt, stay on Phase 1: you lose nothing
but a little manual sweeping, and the server carries no spendable secret at all.
