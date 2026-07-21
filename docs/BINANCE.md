# Binance verification & the payment-safety system

The on-chain gateways (BEP20 / ERC20 / Polygon / TON / LTC) let a buyer pay to a
per-order address you control. **Binance verification is the second rail**: the
buyer pays you through **Binance Pay** (P2P or merchant) or sends an **on-chain
deposit into your Binance account**, submits their order id / txid, and you confirm
it against your *real* Binance history before crediting — using a **read-only** API
key that can never withdraw.

This document covers the whole payment-safety path: verification, the anti-replay
lock, per-seller isolation, the merchant webhook, and the exact order of operations
that makes double-credit impossible.

---

## 1. Why this exists (the threat model)

A "customer submits a reference, operator credits" flow has three classic holes.
This system closes all three:

| Attack | Guard |
|---|---|
| **Fake payment** — submit an id that never paid | Verified against your own Binance history (signed, read-only) |
| **Someone else's payment** — submit a *real* order id that paid a different merchant | **Receiver match**: the row's `receiverInfo` must be *your* Pay ID |
| **Replay** — submit the same real reference twice to credit twice | **Reference registry**: the reference is *burned* before crediting; the second attempt is `already_used` |

Plus: wrong-amount, wrong-currency, unsettled/too-recent, and non-numeric junk are
all rejected.

---

## 2. Setup

1. On Binance → **API Management**, create a key with **only "Enable Reading"**
   (no trade, no withdraw). Restrict it to your server's IP.
2. Configure the gateway:

```bash
OPG_BINANCE_ENABLED=true
OPG_BINANCE_API_KEY=...            # read-only
OPG_BINANCE_API_SECRET=...
OPG_BINANCE_PAY_ID=123456789       # your Binance Pay ID (the receiver) — strongly recommended
OPG_BINANCE_AMOUNT_TOLERANCE=0.50  # USDT band for spot-converted (non-stable) assets
OPG_BINANCE_MIN_AGE_MINUTES=0      # reject payments younger than N minutes (anti-race)
OPG_BINANCE_BASE_URL=https://api.binance.com
```

> **Set `OPG_BINANCE_PAY_ID`.** Without it, the receiver check is skipped and a buyer
> could submit a real order id that actually paid someone else.

---

## 3. What "verified" means

`BinanceVerifier.verify_pay_reference(reference, expected_amount, ...)` returns
`{ok: True, amount, currency, received_at, reference}` **only if every check holds**:

1. **Reference present** in your history. Binance stores the id under different keys
   depending on the flow, so `reference_candidates()` matches on *any* of
   `transactionId / orderId / merchantTradeNo / prepayId / bizId / tradeNo / note`
   (each normalized — copy-paste noise and `0x` prefixes collapse to one key).
2. **Status** is a success state (`PAID / PAY_SUCCESS / SUCCESS / COMPLETED / FINISHED`;
   an absent status means the P2P row is already settled).
3. **Amount** matches — exact, 2-dp floor, or within the autocorrect delta. Non-stable
   assets (BTC/ETH/…) are converted to USDT via the spot price. The *actual* received
   amount is returned (auto-corrected), so a buyer who sends 3.99 for a "$4.00" order
   is credited 3.99, not rejected.
4. **Receiver** matches your `pay_id` (see §1).
5. **Age** ≥ `min_age_minutes` (optional).

If a recent (shallow) scan misses it, the verifier automatically retries with a
**deep scan** (90-day windows, newest-first, up to ~18 months) before returning
`not_found`.

```python
from optimus_gateway.binance import BinanceAccount, BinanceVerifier

acc = BinanceAccount.from_config()          # or BinanceAccount(api_key=..., api_secret=..., pay_id=...)
v = BinanceVerifier(acc)

res = v.verify_pay_reference("443746280424488960", expected_amount=4.00, expected_currency="USDT")
if res["ok"]:
    ...  # res["amount"], res["currency"], res["received_at"]
```

---

## 4. The anti-replay lock (never credit twice)

Verification alone is not enough — a buyer could submit the same *real* reference
again. The **reference registry** (`payment_reference_registry`, a PRIMARY-KEY table)
burns a reference exactly once.

**Always burn before you credit.** The safe one-call path does both:

```python
res = v.verify_and_claim("443746280424488960", expected_amount=4.00)
if res["ok"]:
    credit_user_wallet(res["amount"])       # safe: reference is now burned
elif res["reason"] == "already_used":
    ...                                      # replay — DO NOT credit
```

`verify_and_claim` returns `ok=True` **only if** the payment verified *and* the
reference was not previously used. Lower-level primitives if you manage crediting
yourself:

```python
from optimus_gateway import db
db.claim_reference(ref, "binance_pay")   # -> True if newly claimed, False if replay
db.reference_used(ref)                   # -> True if already burned
db.release_reference(ref)                # un-burn (only if you rolled back before crediting)
```

The same registry protects the on-chain gateways (a txid is burned before it
credits), so **all** payment rails share one replay lock. See
[SECURITY.md](SECURITY.md).

---

## 5. Per-seller ("Direct Payment") mode

A marketplace where each seller collects to **their own** Binance account just uses
one verifier per seller — fully isolated, same code:

```python
seller_acc = BinanceAccount(
    api_key=seller.binance_key, api_secret=seller.binance_secret,
    pay_id=seller.binance_pay_id, label=f"seller:{seller.id}")
res = BinanceVerifier(seller_acc).verify_and_claim(reference, amount)
```

`test_connection()` (calls `/api/v3/account`) validates a seller's key + IP allow-list
before you rely on it.

---

## 6. On-chain deposit into Binance

If the buyer sends crypto **to your Binance deposit address** instead of Binance Pay,
verify the txid the same way:

```python
res = v.verify_deposit_txid("0xabc…", expected_amount=20, expected_address="0xYourBinanceDepositAddr")
```

Checks the deposit reached `status` success (1) or credited-locked (6), the address
matches, and the amount (spot-converted to USDT) is within tolerance.

---

## 7. Binance Pay merchant webhook

If you use the Binance Pay **merchant** API, Binance POSTs an order-status callback
signed HMAC-SHA512 over `timestamp\nnonce\nbody\n`. Verify it **fail-closed** (a
missing secret rejects — never let a forged "paid" webhook credit):

```python
from optimus_gateway.binance import verify_pay_webhook, webhook_status, webhook_trade_no

ok = verify_pay_webhook(raw_body, ts_header, nonce_header, sig_header, secret=your_pay_secret)
if ok and webhook_status(payload) in ("PAY_SUCCESS", "SUCCESS", "PAID"):
    trade_no = webhook_trade_no(payload)
    # still gate crediting on db.claim_reference(trade_no, "binance_pay")
```

Even a signed webhook should be replay-locked on the trade number.

---

## 8. Manual check (CLI)

Verify a reference on demand from the terminal:

```bash
python examples/binance_manual_check.py 443746280424488960 --amount 4.00          # Pay order id
python examples/binance_manual_check.py 443746280424488960 --amount 4.00 --deep    # deep history search
python examples/binance_manual_check.py 0xabc…def --amount 20 --txid               # on-chain deposit
python examples/binance_manual_check.py 443746280424488960 --amount 4.00 --claim   # verify + burn
```

Exit 0 = verified, 1 = not verified, 2 = misconfigured. Read-only unless `--claim`.

---

## 9. The golden order of operations

```
verify (signed, read-only)  →  receiver + amount + status + age all pass
        →  claim_reference() burns it (False = replay → stop)
        →  credit the user
        →  (optional) deliver / fulfil
```

Never credit before the reference is burned. That single rule — shared by Binance
Pay, Binance deposits, and every on-chain gateway — is what makes this system
double-credit-proof.
