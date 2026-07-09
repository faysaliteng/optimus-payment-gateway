# Optimus Payment Gateway — Merchant API

REST reference for the merchant-facing API and the signed webhooks the gateway
sends you. This is a **server-to-server** API: you call it from your backend
(bot, web shop, ERP), never from a browser, because it is authenticated with a
shared secret.

- **Version:** `1.0.0`
- **Content type:** `application/json` for all request/response bodies.
- **Money:** amounts are quoted in your fiat `OPG_QUOTE_CURRENCY` (default `USD`)
  and settled 1:1 in the stablecoin (1 USD = 1 USDT). Internally everything is
  **integer cents** — there is never a floating-point rounding bug.
- **Networks (`method` keys):** `usdt_bep20`, `usdt_erc20`, `usdt_polygon`,
  `usdt_ton`. Only the methods listed in `OPG_ENABLED_METHODS` are accepted.

> New here? Read [`INTEGRATION.md`](INTEGRATION.md) for a 10-minute
> copy-paste integration and a ready-made client class.

---

## Base URL

Everything is served from the URL you configured in `OPG_BASE_URL`:

```
https://pay.yourdomain.com
```

| Purpose | Method & path |
|---|---|
| Create a payment | `POST /api/v1/order/create` |
| Query a payment | `GET  /api/v1/order/{trade_id}` |
| Hosted checkout page (payer-facing) | `GET  /pay/{trade_id}` |
| Checkout status JSON (browser polls this) | `GET  /pay/{trade_id}/status` |
| Payment QR image | `GET  /pay/{trade_id}/qr.png` |
| Health / config summary | `GET  /health` |

The `/api/v1/*` endpoints are for your backend. The `/pay/*` endpoints are safe
to expose to the buyer (they contain no secrets and take only the `trade_id`).

---

## Authentication & request signing

Set these two values on the gateway (`.env`) and share them with your backend:

```ini
OPG_MERCHANT_API_KEY=pk_live_optimus            # public identifier
OPG_MERCHANT_API_SECRET=sk_test_supersecret_change_me   # NEVER leave your server
```

Every call to `POST /api/v1/order/create` must include:

1. `api_key` — your public key, sent as a body field.
2. `signature` — an **HMAC-SHA256** of the request body, computed with your
   secret.

> If `OPG_MERCHANT_API_KEY` is left empty on the gateway, authentication is
> **disabled** (single-tenant / trusted-network mode) and neither field is
> required. For any internet-facing deployment, set both.

### How the signature is computed

The signature is `sign_params(secret, body)`:

1. Take every field in the request body **except** `signature` and `sign`.
2. Drop any field whose value is `null` or an empty string `""`.
3. Sort the remaining fields by key name (ASCII/ascending).
4. Join them as `key=value` pairs with `&` (no URL-encoding, no trailing `&`).
   Each value is its plain string form.
5. `HMAC_SHA256(secret, that_string)` → lowercase hex digest.

The gateway recomputes the exact same digest over the body it receives and
rejects the request if it doesn't match. **The `api_key` field is part of the
signed string**, so a tampered key invalidates the signature.

> **Determinism rule (important):** the gateway signs the values *after* they
> survive JSON parsing, so send every signed value as a **string** (e.g.
> `"amount": "25.00"`, not `25`). That guarantees your client and the gateway
> hash byte-identical strings regardless of language. If you need to attach
> structured `metadata`, JSON-encode it into a string yourself.

#### Worked example

Body (before adding `signature`):

```json
{
  "api_key": "pk_live_optimus",
  "method": "usdt_bep20",
  "amount": "25.00",
  "order_id": "INV-1042",
  "notify_url": "https://shop.example.com/webhooks/optimus"
}
```

Canonical string to sign (keys sorted, `&`-joined):

```
amount=25.00&api_key=pk_live_optimus&method=usdt_bep20&notify_url=https://shop.example.com/webhooks/optimus&order_id=INV-1042
```

With `secret = "sk_test_supersecret_change_me"` this yields:

```
signature = 536a7a28d73d4812dba4da9bb91b0e1619c89b89e5a7d30a3cd9803ad3cee172
```

(You can reproduce that value with either snippet below.)

### Python

```python
import hashlib
import hmac

def sign_params(secret: str, params: dict) -> str:
    msg = "&".join(
        f"{k}={params[k]}"
        for k in sorted(params)
        if k not in ("signature", "sign") and params[k] not in (None, "")
    )
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

body = {
    "api_key": "pk_live_optimus",
    "method": "usdt_bep20",
    "amount": "25.00",
    "order_id": "INV-1042",
    "notify_url": "https://shop.example.com/webhooks/optimus",
}
body["signature"] = sign_params("sk_test_supersecret_change_me", body)
# -> 536a7a28d73d4812dba4da9bb91b0e1619c89b89e5a7d30a3cd9803ad3cee172
```

### Node.js

```javascript
const crypto = require("crypto");

function signParams(secret, params) {
  const msg = Object.keys(params)
    .filter((k) => k !== "signature" && k !== "sign")
    .filter((k) => params[k] !== null && params[k] !== undefined && params[k] !== "")
    .sort()
    .map((k) => `${k}=${params[k]}`)
    .join("&");
  return crypto.createHmac("sha256", secret).update(msg).digest("hex");
}

const body = {
  api_key: "pk_live_optimus",
  method: "usdt_bep20",
  amount: "25.00",
  order_id: "INV-1042",
  notify_url: "https://shop.example.com/webhooks/optimus",
};
body.signature = signParams("sk_test_supersecret_change_me", body);
// -> 536a7a28d73d4812dba4da9bb91b0e1619c89b89e5a7d30a3cd9803ad3cee172
```

---

## POST /api/v1/order/create

Reserve a payment. The gateway allocates the receiving address (or a unique
amount, or a TON memo — depending on how it is configured) and returns
everything the payer needs.

### Request body

| Field | Type | Required | Description |
|---|---|:---:|---|
| `api_key` | string | ✅¹ | Your public key (`OPG_MERCHANT_API_KEY`). |
| `signature` | string | ✅¹ | HMAC-SHA256 of the body (see above). |
| `method` | string | ✅ | Network/token to charge on: `usdt_bep20`, `usdt_erc20`, `usdt_polygon`, `usdt_ton`. Must be in `OPG_ENABLED_METHODS`. |
| `amount` | string\|number | ✅ | Amount to charge in the quote currency (USD). Send as a `"25.00"` string for deterministic signing. Must be `> 0`. |
| `order_id` | string | ➖ | **Your** order id. Used as the idempotency key and echoed back as `merchant_order_id`. Highly recommended. (`merchant_order_id` is accepted as an alias.) |
| `notify_url` | string | ➖ | HTTPS URL we POST the signed webhook to when the order is paid. |
| `redirect_url` | string | ➖ | Where your checkout should send the buyer after payment (stored & echoed; the hosted page does not auto-redirect). |
| `metadata` | string | ➖ | Opaque value stored with the order. JSON-encode structured data into a string yourself so it signs deterministically. |

¹ Required unless the gateway runs with authentication disabled (no
`OPG_MERCHANT_API_KEY` set).

### Example request

```bash
curl -s https://pay.yourdomain.com/api/v1/order/create \
  -H 'content-type: application/json' \
  -d '{
    "api_key": "pk_live_optimus",
    "method": "usdt_bep20",
    "amount": "25.00",
    "order_id": "INV-1042",
    "notify_url": "https://shop.example.com/webhooks/optimus",
    "signature": "536a7a28d73d4812dba4da9bb91b0e1619c89b89e5a7d30a3cd9803ad3cee172"
  }'
```

### Example response `200 OK`

Every successful API response is wrapped in `{ "status_code": 200, "data": … }`.

```json
{
  "status_code": 200,
  "data": {
    "trade_id": "kQ7mVn2pR8sT1uW4xY6zA9bC",
    "merchant_order_id": "INV-1042",
    "method": "usdt_bep20",
    "network": "USDT (BEP20 / BSC)",
    "token": "USDT",
    "quote_amount": 25.0,
    "quote_currency": "USD",
    "pay_amount": "25.00",
    "pay_amount_cents": 2500,
    "received_cents": 0,
    "pay_address": "0x9f3ca6b2e1d0475a8c2f4e6b1a9d8c7e5f2b3a4d",
    "pay_memo": null,
    "payment_uri": "0x9f3ca6b2e1d0475a8c2f4e6b1a9d8c7e5f2b3a4d",
    "status": "pending",
    "expires_at": "2026-07-09 12:20:00",
    "checkout_url": "https://pay.yourdomain.com/pay/kQ7mVn2pR8sT1uW4xY6zA9bC",
    "explorer": "https://bscscan.com/tx/",
    "created_at": "2026-07-09 11:40:00",
    "paid_at": null
  }
}
```

### Response `data` fields

| Field | Type | Description |
|---|---|---|
| `trade_id` | string | The gateway's public order id. Use it for `GET /order/{trade_id}` and `/pay/{trade_id}`. |
| `merchant_order_id` | string \| null | Echo of your `order_id`. |
| `method` | string | The method key you requested. |
| `network` | string | Human label, e.g. `"USDT (BEP20 / BSC)"`. Show this to the buyer. |
| `token` | string | Always `"USDT"`. |
| `quote_amount` | number | The fiat amount you quoted. |
| `quote_currency` | string | e.g. `"USD"`. |
| `pay_amount` | string | **The exact amount the buyer must send**, as a 2-dp string. See the note on amount-match mode below. |
| `pay_amount_cents` | integer | `pay_amount` in cents (`2500`). |
| `received_cents` | integer | Running total credited so far (`0` until a transfer lands). |
| `pay_address` | string | The address to pay. Per-order-unique in xpub mode; shared in amount-match mode. |
| `pay_memo` | string \| null | **TON only** — the required transfer comment/memo. `null` on EVM chains. |
| `payment_uri` | string | Wallet/QR payload. On EVM this is the plain address; on TON a `ton://transfer/…?text=<memo>` deep link. |
| `status` | string | `pending` \| `paid` \| `expired`. See [Order statuses](#order-statuses). |
| `expires_at` | string | UTC `YYYY-MM-DD HH:MM:SS` when the reservation lapses (`OPG_RESERVATION_TTL_MINUTES`, default 40). |
| `checkout_url` | string | Hosted payment page — redirect the buyer here, or build your own UI. |
| `explorer` | string | Block-explorer tx base URL for this chain. |
| `created_at` | string | UTC creation timestamp. |
| `paid_at` | string \| null | UTC timestamp when the order flipped to `paid` (else `null`). |

> **Amount-match mode:** if the gateway is configured with a single shared
> address (`OPG_SHARED_RECEIVE_ADDRESS`) instead of an xpub, the gateway nudges
> `pay_amount` up by a cent or two so the on-chain amount uniquely identifies the
> order. **Always display `pay_amount`, not your original quote** — the buyer
> must send that exact amount. In per-order-address (xpub) mode `pay_amount`
> equals your quote.

### Idempotency

`order_id` (`merchant_order_id`) is a **unique key**. If you POST
`/order/create` again with an `order_id` that already exists, the gateway does
**not** create a second order — it returns the **existing** order unchanged
(same `trade_id`, same address, same amount). This makes create safe to retry on
a network timeout: reuse the same `order_id` and you'll converge on one payment.

Omitting `order_id` creates a brand-new order every call, so always pass a stable
`order_id` for real checkouts.

---

## GET /api/v1/order/{trade_id}

Query the current state of an order. Poll this if you can't receive webhooks (see
[polling vs webhooks](INTEGRATION.md#webhooks-vs-polling)).

### Example

```bash
curl -s https://pay.yourdomain.com/api/v1/order/kQ7mVn2pR8sT1uW4xY6zA9bC
```

### Response `200 OK`

Identical shape to create — `{ "status_code": 200, "data": { …same fields… } }`
— with live `status`, `received_cents`, and `paid_at`:

```json
{
  "status_code": 200,
  "data": {
    "trade_id": "kQ7mVn2pR8sT1uW4xY6zA9bC",
    "merchant_order_id": "INV-1042",
    "method": "usdt_bep20",
    "network": "USDT (BEP20 / BSC)",
    "token": "USDT",
    "pay_amount": "25.00",
    "pay_amount_cents": 2500,
    "received_cents": 2500,
    "pay_address": "0x9f3ca6b2e1d0475a8c2f4e6b1a9d8c7e5f2b3a4d",
    "status": "paid",
    "expires_at": "2026-07-09 12:20:00",
    "checkout_url": "https://pay.yourdomain.com/pay/kQ7mVn2pR8sT1uW4xY6zA9bC",
    "explorer": "https://bscscan.com/tx/",
    "created_at": "2026-07-09 11:40:00",
    "paid_at": "2026-07-09 11:52:13"
  }
}
```

This endpoint is **unauthenticated** (the `trade_id` is an unguessable 24-char
token). It exposes no secrets and no other order's data.

`404` if the `trade_id` is unknown: `{ "detail": "order not found" }`.

---

## Payer-facing endpoints (`/pay/*`)

These render the hosted checkout. Point the buyer's browser at them; they take
only the `trade_id`.

### GET /pay/{trade_id}

A self-contained HTML checkout page: the amount, network, the pay-to address (and
TON memo, when applicable), an embedded QR, click-to-copy, and a live status
pill. The page auto-polls `/pay/{trade_id}/status` every 4 seconds and updates
itself to **"Payment received"** the moment the order is credited. Returns `404`
for an unknown `trade_id`.

### GET /pay/{trade_id}/status

Lightweight JSON the checkout page polls. **Not** wrapped in `status_code`:

```json
{
  "trade_id": "kQ7mVn2pR8sT1uW4xY6zA9bC",
  "status": "pending",
  "received_cents": 0,
  "expected_cents": 2500
}
```

`received_cents` climbs as transfers land; when it reaches `expected_cents` the
`status` becomes `paid`. Unknown id → `404 { "error": "not_found" }`.

You *may* poll this from your own backend, but for authoritative server-side
state prefer `GET /api/v1/order/{trade_id}` (it returns the full order).

### GET /pay/{trade_id}/qr.png

A PNG QR code encoding the `pay_address` (`image/png`). Embed it directly:
`<img src="https://pay.yourdomain.com/pay/{trade_id}/qr.png">`. Requires `segno`
on the server; returns `500` if QR rendering is unavailable, `404` for an unknown
id.

---

## GET /health

Ops endpoint. No auth. Returns the running version and a **non-secret** config
summary — handy for load-balancer health checks and verifying which methods are
live.

```json
{
  "ok": true,
  "version": "1.0.0",
  "config": {
    "base_url": "https://pay.yourdomain.com",
    "quote_currency": "USD",
    "enabled_methods": ["usdt_bep20", "usdt_polygon"],
    "accept_usdc": true,
    "per_order_address_mode": true,
    "amount_match_mode": false,
    "auto_sweep": true,
    "sweep_destination_set": true,
    "binance_verify": false,
    "ton_enabled": false,
    "min_confirmations": 3,
    "reservation_ttl_minutes": 40
  }
}
```

---

## Order statuses

| `status` | Meaning |
|---|---|
| `pending` | Reserved and waiting for payment. The address/amount/memo is live until `expires_at`. |
| `paid` | The credited total reached `pay_amount_cents`. **This is your settlement signal.** `paid_at` is set. |
| `expired` | The reservation TTL lapsed with insufficient funds and the order was closed. |

Notes on money edge-cases (all handled for you, none lose funds):

- **Partial payments** accumulate: `received_cents` grows with each transfer and
  the order flips to `paid` only once the total covers `pay_amount_cents`.
- **Overpayment** still settles as `paid`; the excess is recorded (the credit is
  never rejected).
- **Late payments** arriving shortly after expiry are still credited within the
  cooldown window (`OPG_AMOUNT_COOLDOWN_MINUTES`, default 24 h) — you'll receive
  the webhook then.
- Every on-chain txid is **burned before crediting**, so re-scans and retries can
  never double-credit.

---

## Webhooks (callbacks to your `notify_url`)

When an order becomes **fully paid**, the gateway POSTs a signed JSON callback to
the order's `notify_url`. Deliveries are queued and retried with exponential
backoff (30 s, 60 s, 120 s, … capped at 1 h) up to `OPG_WEBHOOK_MAX_RETRIES`
(default 6) until your endpoint returns a `2xx`. Request timeout is
`OPG_WEBHOOK_TIMEOUT` (default 12 s).

### Delivery

- **Method:** `POST`
- **Headers:**
  - `Content-Type: application/json`
  - `X-OPG-Signature: <hmac_sha256_hex>` — the same digest as the body's
    `signature` field.
  - `User-Agent: OptimusGateway/1.0`

### Payload

```json
{
  "event": "payment.completed",
  "trade_id": "kQ7mVn2pR8sT1uW4xY6zA9bC",
  "merchant_order_id": "INV-1042",
  "method": "usdt_bep20",
  "status": "paid",
  "amount": "25.00",
  "amount_cents": 2500,
  "received_cents": 2500,
  "pay_address": "0x9f3ca6b2e1d0475a8c2f4e6b1a9d8c7e5f2b3a4d",
  "tx_hashes": "0x5b1e7d5a2f0c9e3b8a1d4c6f7e2b9a0d3c5e8f1a2b4c6d8e0f1a3b5c7d9e0f2a",
  "timestamp": 1752060000000,
  "signature": "92bc486a361f083417e2581bfe860078b268bc4c5efe766bf3393718c45059ff"
}
```

| Field | Type | Description |
|---|---|---|
| `event` | string | `payment.completed` when `status` is `paid` (the normal case), otherwise `payment.updated`. |
| `trade_id` | string | Gateway order id. |
| `merchant_order_id` | string \| null | Your order id. |
| `method` | string | Network/token the payment settled on. |
| `status` | string | Order status at send time — `paid` for a completed payment. |
| `amount` | string | Expected amount as a 2-dp string (`"25.00"`). |
| `amount_cents` | integer | Expected amount in cents. |
| `received_cents` | integer | Total actually credited (≥ `amount_cents` when paid; larger on overpayment). |
| `pay_address` | string | The receiving address that got paid. |
| `tx_hashes` | string | Comma-separated on-chain txids that funded this order (may be empty). |
| `timestamp` | integer | Delivery time, Unix epoch **milliseconds**. |
| `signature` | string | HMAC-SHA256 of the payload (identical scheme to request signing). |

### Verifying a webhook

The signature is computed over the payload **before** the `signature` field is
added, using the very same `sign_params` rules (drop `signature`/`sign` and any
`null`/`""` value, sort keys, join `key=value` with `&`, HMAC-SHA256). Because
`sign_params` already skips the `signature` key, you can hand it the entire
received JSON and compare in constant time:

```python
import hashlib
import hmac

def sign_params(secret: str, params: dict) -> str:
    msg = "&".join(
        f"{k}={params[k]}"
        for k in sorted(params)
        if k not in ("signature", "sign") and params[k] not in (None, "")
    )
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

def verify_webhook(secret: str, payload: dict, header_sig: str) -> bool:
    expected = sign_params(secret, payload)      # payload's own `signature` is ignored
    given = payload.get("signature") or header_sig
    return hmac.compare_digest(str(given or ""), expected)   # constant-time
```

Node.js:

```javascript
const crypto = require("crypto");

function signParams(secret, params) {
  const msg = Object.keys(params)
    .filter((k) => k !== "signature" && k !== "sign")
    .filter((k) => params[k] !== null && params[k] !== undefined && params[k] !== "")
    .sort()
    .map((k) => `${k}=${params[k]}`)
    .join("&");
  return crypto.createHmac("sha256", secret).update(msg).digest("hex");
}

function verifyWebhook(secret, payload, headerSig) {
  const expected = signParams(secret, payload);
  const given = payload.signature || headerSig || "";
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
```

> The webhook payload contains only strings and integers (no floats, booleans, or
> nested objects), so `String(value)` reproduces Python's `str(value)` exactly —
> the digest matches across languages with no special formatting.

### Webhook best practices

- **Verify the signature first** and reject anything that fails. Never trust an
  unsigned or mismatched callback.
- **Respond `2xx` fast** (ideally after just recording the event). Slow or
  error responses trigger retries.
- **Be idempotent.** A retried delivery, or a late top-up, can deliver the same
  `trade_id` more than once. Treat `status == "paid"` as terminal and ignore
  repeats (e.g. key on `trade_id`, or only act if your order isn't already
  fulfilled).
- **Reconcile independently.** Fulfil against your own record keyed by
  `merchant_order_id`/`trade_id`; if in doubt, confirm with
  `GET /api/v1/order/{trade_id}`.

---

## Errors

Errors use standard HTTP status codes. `4xx`/`5xx` responses are FastAPI's
shape — `{ "detail": "<message>" }` — **not** the `status_code`-wrapped success
envelope.

| HTTP | `detail` | When |
|---|---|---|
| `400` | `amount must be a number` | `amount` missing or non-numeric. |
| `400` | `amount must be > 0` | Amount is zero or negative. |
| `400` | `unknown method 'xyz'` | `method` is not a known chain key. |
| `400` | `method 'xyz' is not enabled` | Method not in `OPG_ENABLED_METHODS`. |
| `400` | `OPG_TON_ADDRESS not configured` | TON method requested but no TON address configured. |
| `400` | `Configure OPG_GATEWAY_XPUB … or OPG_SHARED_RECEIVE_ADDRESS …` | No EVM receiving mode configured. |
| `401` | `bad api_key` | `api_key` missing or wrong (auth enabled). |
| `401` | `bad signature` | Signature didn't match the body. |
| `404` | `order not found` | Unknown `trade_id` (order/query/qr endpoints). |
| `404` | `{ "error": "not_found" }` | Unknown `trade_id` on `/pay/{id}/status` (note: different shape). |
| `500` | `qr unavailable (pip install segno)` | QR rendering dependency missing. |

Example:

```json
{ "detail": "bad signature" }
```

---

## Field & value cheat-sheet

| Concept | Value(s) |
|---|---|
| Methods | `usdt_bep20` · `usdt_erc20` · `usdt_polygon` · `usdt_ton` |
| Success envelope | `{ "status_code": 200, "data": { … } }` |
| Error envelope | `{ "detail": "…" }` (and `{ "error": "not_found" }` on `/pay/*/status`) |
| Money units | fiat amount in `amount`/`pay_amount`; cents in `*_cents` |
| Timestamps (orders) | UTC `YYYY-MM-DD HH:MM:SS` strings |
| Timestamp (webhook) | Unix epoch **ms** integer |
| Signature | HMAC-SHA256 hex over sorted `key=value&…` (excl. `signature`/`sign` and empty values) |
| Webhook header | `X-OPG-Signature` |
| Idempotency key | `order_id` (`merchant_order_id`) |

See [`INTEGRATION.md`](INTEGRATION.md) for an end-to-end walkthrough and a
drop-in client, and [`../examples/`](../examples/) for runnable samples.
</content>
</invoke>
