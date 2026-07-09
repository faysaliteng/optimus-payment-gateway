# Add crypto payments to your app in 10 minutes

This guide takes you from zero to a working crypto checkout: create an order, show
the buyer an address/QR (or redirect to the hosted page), and get a signed webhook
the instant they pay. It includes a **copy-paste Python client** that handles
request signing and webhook verification for you.

For the exhaustive field-by-field reference see [`API.md`](API.md).

---

## The flow

```
  ┌── your backend ──┐        ┌──── Optimus Gateway ────┐        ┌── chain ──┐
  │                  │  1     │                          │        │           │
  │  create order ───┼───────►│ reserve + derive address │        │  BSC/ETH  │
  │                  │◄───────┤ returns address + amount │        │  Polygon  │
  │  show address/QR │  2     │        + checkout_url    │        │   TON     │
  │  OR redirect ────┼──────────────► hosted /pay/{id} ──┼───────►│  buyer    │
  │                  │                                    │◄───────┤  pays     │
  │                  │  3   signed webhook (X-OPG-Sig)    │ watcher│           │
  │  mark order PAID │◄───────────────────────────────────┤ credit │           │
  └──────────────────┘                                    └────────┴───────────┘
```

1. **Create** — `POST /api/v1/order/create` with the amount and your `order_id`.
   You get back a `pay_address`, the exact `pay_amount`, a `trade_id`, and a
   `checkout_url`.
2. **Collect payment** — either render your own UI (address + QR at
   `/pay/{trade_id}/qr.png`) **or** redirect the buyer to the hosted
   `checkout_url`, which shows everything and live-updates itself.
3. **Get paid** — the gateway's watcher sees the on-chain transfer, credits the
   order, and POSTs a **signed webhook** to your `notify_url`. Verify the
   signature, mark your order paid, deliver the goods.

That's the whole integration. Everything below is detail and copy-paste code.

---

## Prerequisites (2 minutes)

You need the gateway running (see the project [README](../README.md)) and two
shared credentials from its `.env`:

```ini
OPG_MERCHANT_API_KEY=pk_live_optimus
OPG_MERCHANT_API_SECRET=sk_test_supersecret_change_me   # keep on your server only
```

Confirm it's up and which methods are live:

```bash
curl -s https://pay.yourdomain.com/health
# -> { "ok": true, "config": { "enabled_methods": ["usdt_bep20", ...], ... } }
```

---

## Step 1 — a drop-in Python client

Save this as `optimus_client.py`. It signs create-order requests and verifies
webhooks using the **exact** scheme the gateway uses (`sign_params`: HMAC-SHA256
over the sorted `key=value&…` body, excluding `signature`/`sign` and empty
values). No third-party dependencies beyond `requests`.

```python
"""Minimal Optimus Payment Gateway client (create orders + verify webhooks)."""
from __future__ import annotations

import hashlib
import hmac
import json

import requests


class OptimusClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout

    # --- signing (identical to the gateway's security.sign_params) ----------
    def _sign(self, params: dict) -> str:
        msg = "&".join(
            f"{k}={params[k]}"
            for k in sorted(params)
            if k not in ("signature", "sign") and params[k] not in (None, "")
        )
        return hmac.new(self.api_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    # --- create a payment ---------------------------------------------------
    def create_order(self, method: str, amount, order_id: str, *,
                     notify_url: str | None = None, redirect_url: str | None = None,
                     metadata=None) -> dict:
        # Send every signed value as a STRING so the digest is deterministic.
        body = {
            "api_key": self.api_key,
            "method": method,
            "amount": f"{float(amount):.2f}",
            "order_id": str(order_id),
        }
        if notify_url:
            body["notify_url"] = notify_url
        if redirect_url:
            body["redirect_url"] = redirect_url
        if metadata is not None:
            body["metadata"] = metadata if isinstance(metadata, str) \
                else json.dumps(metadata, separators=(",", ":"), sort_keys=True)
        body["signature"] = self._sign(body)

        r = requests.post(f"{self.base_url}/api/v1/order/create",
                          json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()["data"]

    # --- query a payment ----------------------------------------------------
    def get_order(self, trade_id: str) -> dict:
        r = requests.get(f"{self.base_url}/api/v1/order/{trade_id}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()["data"]

    # --- verify an inbound webhook -----------------------------------------
    def verify_webhook(self, payload: dict, header_signature: str = "") -> bool:
        expected = self._sign(payload)  # sign_params ignores the payload's own `signature`
        given = payload.get("signature") or header_signature or ""
        return hmac.compare_digest(str(given), expected)
```

Use it:

```python
opg = OptimusClient(
    "https://pay.yourdomain.com",
    api_key="pk_live_optimus",
    api_secret="sk_test_supersecret_change_me",
)

order = opg.create_order(
    "usdt_bep20", 25.00, order_id="INV-1042",
    notify_url="https://shop.example.com/webhooks/optimus",
)
print(order["pay_address"], order["pay_amount"], order["checkout_url"])
```

---

## Step 2 — show the payment (two options)

**Option A — redirect to the hosted checkout (simplest).** Send the buyer to
`order["checkout_url"]`. That page shows the amount, network, address, QR, and TON
memo (when relevant), and live-updates to "Payment received" on its own.

```python
return redirect(order["checkout_url"])
```

**Option B — render it yourself.** Show `pay_amount` + `pay_address` and embed the
QR straight from the gateway:

```html
<h3>Send exactly {{ order.pay_amount }} USDT on {{ order.network }}</h3>
<img src="{{ order.checkout_url }}/qr.png" alt="Pay QR" width="200" height="200">
<code>{{ order.pay_address }}</code>
{% if order.pay_memo %}<p>MEMO (required): <code>{{ order.pay_memo }}</code></p>{% endif %}
```

> **Always show `pay_amount`, not your original price.** In amount-match mode the
> gateway may bump it by a cent to make the amount unique. On **TON** the
> `pay_memo` is mandatory — a transfer without it can't be matched.

---

## Step 3 — receive the signed webhook

Point `notify_url` at an HTTPS endpoint. On payment you'll get a `POST` with an
`X-OPG-Signature` header and a signed JSON body. Verify, then fulfil.

Flask:

```python
from flask import Flask, request, abort

app = Flask(__name__)
opg = OptimusClient("https://pay.yourdomain.com", "pk_live_optimus",
                    "sk_test_supersecret_change_me")

@app.post("/webhooks/optimus")
def optimus_webhook():
    payload = request.get_json(force=True)
    sig = request.headers.get("X-OPG-Signature", "")

    if not opg.verify_webhook(payload, sig):
        abort(401)                              # reject forgeries

    if payload.get("status") == "paid":
        order_id = payload["merchant_order_id"] # your id
        # IDEMPOTENT: ignore if already fulfilled (retries + late top-ups happen)
        if not already_fulfilled(order_id):
            mark_paid_and_deliver(order_id, payload)

    return "", 200                              # 2xx ACK stops retries
```

FastAPI:

```python
from fastapi import FastAPI, Request, HTTPException

api = FastAPI()

@api.post("/webhooks/optimus")
async def optimus_webhook(request: Request):
    payload = await request.json()
    sig = request.headers.get("X-OPG-Signature", "")
    if not opg.verify_webhook(payload, sig):
        raise HTTPException(401, "bad signature")
    if payload.get("status") == "paid" and not already_fulfilled(payload["merchant_order_id"]):
        mark_paid_and_deliver(payload["merchant_order_id"], payload)
    return {"ok": True}
```

**Three rules that matter:**

1. **Verify first, always.** Never act on an unverified body.
2. **Return `2xx` quickly.** Non-2xx/timeouts are retried with exponential
   backoff (up to 6 times). Do the heavy lifting after you ACK, or make it fast.
3. **Be idempotent.** The same `trade_id` may arrive more than once (a retry, or
   a late top-up). Key on `merchant_order_id`/`trade_id` and no-op if you already
   fulfilled it.

---

## Webhooks vs polling

Webhooks are push-based and near-instant — **prefer them**. But if your webhook
endpoint isn't reachable yet (local dev, no public URL) you can poll instead:

```python
import time

def wait_for_payment(opg, trade_id, timeout_s=1800, interval_s=5):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        o = opg.get_order(trade_id)
        if o["status"] in ("paid", "expired"):
            return o["status"]
        time.sleep(interval_s)
    return "timeout"
```

Guidance:

- **Production:** webhooks, with polling only as a reconciliation backstop for the
  rare missed callback.
- **Poll `GET /api/v1/order/{trade_id}`** for authoritative backend state (it
  returns the full order). `GET /pay/{trade_id}/status` is a lighter endpoint meant
  for the checkout page's own JS.
- **Don't hammer.** A 5–10 s interval is plenty; on-chain confirmations take longer
  than that anyway.
- **Belt-and-suspenders:** use webhooks to react instantly *and* a periodic
  reconcile job that re-queries any still-`pending` orders — both paths are
  idempotent, so it's safe to run both.

---

## Testing tips

- **Start on a shared address / amount-match or small amounts.** Send a real but
  tiny transfer (e.g. `amount: "0.50"`) end-to-end before wiring up big orders.
- **Testnets.** Point the chain RPCs at a testnet and use a testnet xpub/address to
  rehearse the full flow with valueless coins before mainnet.
- **Verify the signature helper offline.** Sign a known body and confirm you get
  the reference digest from [`API.md`](API.md#worked-example)
  (`536a7a28…cee172`) — if that matches, your signing is correct.
- **Test webhook delivery locally** by tunnelling your dev server (e.g. an ngrok
  URL) as the `notify_url`, or just poll `get_order` while developing.
- **Exercise idempotency.** Create with the same `order_id` twice — you should get
  the **same** `trade_id` back (no duplicate charge). Deliver a webhook to
  yourself twice — your handler should fulfil once.
- **Confirm the exact amount.** In amount-match mode, verify your UI shows
  `pay_amount` (which may differ from the quote by a cent), not your raw price.
- **Watch the logs.** `GET /health` shows which methods are enabled and whether
  per-order-address vs amount-match mode is active — mismatches here are the most
  common "why didn't it credit" cause.

---

## Checklist

- [ ] `create_order` returns a `pay_address` + `pay_amount` + `checkout_url`.
- [ ] Buyer UI shows **`pay_amount`** (and the TON `pay_memo` if present).
- [ ] `notify_url` endpoint verifies `X-OPG-Signature` and returns `2xx`.
- [ ] Fulfilment is **idempotent** on `merchant_order_id`/`trade_id`.
- [ ] A reconcile/poll job catches any missed webhook.
- [ ] Secrets (`OPG_MERCHANT_API_SECRET`) never leave your backend.

---

## More

- [`API.md`](API.md) — full REST + webhook reference, every field, error codes.
- [`../examples/`](../examples/) — runnable sample integrations.
- [`../README.md`](../README.md) — running and configuring the gateway itself.
</content>
