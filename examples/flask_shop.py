#!/usr/bin/env python
"""
==============================================================================
 Optimus Payment Gateway  —  Flask shop example  (minimal, copy & adapt)
==============================================================================

A tiny web shop with exactly one product and a "Pay with crypto" button:

    GET  /            product page + Pay button
    POST /buy         create a payment, redirect to the hosted checkout page
    GET  /thanks      where the payer lands after paying (redirect_url)
    POST /webhook     the gateway's signed server-to-server callback — VERIFIED
                      here before the order is marked paid
    GET  /order/<id>  tiny status view (polls the gateway)

Like the Telegram example, this uses the gateway as a LOCAL library, so this
process IS the gateway. For payments to actually be detected you must ALSO run
the watcher (either `python run.py serve` against the same OPG_DB_PATH, or start
`server.workers.start_background()` in-process — uncomment it in main()).

The important, reusable bit is /webhook: it shows EXACTLY how any merchant (even
one on another stack/host) verifies our callback signature before trusting it.

Run it:

    pip install flask                            # plus: pip install -r requirements.txt
    export OPG_GATEWAY_XPUB=xpub6C...            # your watch-only receiving xpub
    export OPG_ENABLED_METHODS=usdt_bep20
    export OPG_BASE_URL=http://localhost:5000    # so checkout/redirect links resolve
    export OPG_ALLOW_PRIVATE_WEBHOOKS=true       # THIS DEMO posts its webhook to localhost;
                                                 # the SSRF guard blocks private notify_url
                                                 # by default — only relax it for local dev
    export OPG_MERCHANT_API_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))")
    python examples/flask_shop.py
"""
from __future__ import annotations

import logging
import os
import time

from flask import Flask, abort, redirect, render_template_string, request

# --- the gateway, used as a local library -----------------------------------
from optimus_gateway import create_payment, get_payment, init
from optimus_gateway.config import config
from optimus_gateway.security import constant_time_equals, sign_params

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("example.flask_shop")

app = Flask(__name__)
init()  # create the gateway DB (idempotent)

# --- what you're selling (edit me) ------------------------------------------
PRODUCT = {"name": "Optimus Pro — 1 month", "price_usd": 25.00}
PAY_METHOD = os.getenv("OPG_ENABLED_METHODS", "usdt_bep20").split(",")[0].strip()

# The shared secret the gateway signs webhooks with. In a REAL deployment the
# gateway and this shop are separate; both hold this secret out-of-band. Here we
# read the same config the in-process gateway uses.
WEBHOOK_SECRET = config.MERCHANT_API_SECRET

# Toy "order book" so the demo can show fulfilment. Use a real DB in production.
ORDERS: dict[str, dict] = {}   # trade_id -> {status, merchant_order_id, ...}


# ---------------------------------------------------------------------------
#  Storefront
# ---------------------------------------------------------------------------
_HOME = """<!doctype html><meta charset=utf-8>
<title>{{ p.name }}</title>
<style>body{font-family:system-ui,sans-serif;max-width:520px;margin:64px auto;padding:0 16px}
.card{border:1px solid #e2e2e2;border-radius:14px;padding:24px}
button{background:#111;color:#fff;border:0;border-radius:10px;padding:12px 20px;font-size:15px;cursor:pointer}
.price{font-size:32px;font-weight:800}</style>
<div class=card>
  <h1>{{ p.name }}</h1>
  <p class=price>${{ '%.2f'|format(p.price_usd) }}</p>
  <p>Pay with USDT on {{ method }}. You'll get a QR + address on the next screen.</p>
  <form method=post action="/buy"><button type=submit>Pay with crypto →</button></form>
</div>"""


@app.get("/")
def home():
    return render_template_string(_HOME, p=PRODUCT, method=PAY_METHOD)


@app.post("/buy")
def buy():
    # A unique merchant order id makes create_payment idempotent (double-submit safe).
    merchant_order_id = f"web-{int(time.time() * 1000)}"
    base = config.BASE_URL
    order = create_payment(
        PAY_METHOD,
        PRODUCT["price_usd"],
        merchant_order_id=merchant_order_id,
        notify_url=f"{base}/webhook",     # where the gateway will POST when paid
        redirect_url=f"{base}/thanks",    # where the hosted checkout sends the payer
        metadata={"product": PRODUCT["name"]},
    )
    # Remember it locally so /webhook can flip it to paid + we can fulfil.
    ORDERS[order["trade_id"]] = {
        "status": order["status"],
        "merchant_order_id": merchant_order_id,
        "amount": order["pay_amount"],
        "network": order["network"],
    }
    log.info("created order %s -> %s", order["trade_id"], order["checkout_url"])
    # Hand the payer to the gateway's hosted checkout (address + live QR + polling).
    return redirect(order["checkout_url"], code=303)


@app.get("/thanks")
def thanks():
    return (
        "<h2>Thanks! 🎉</h2><p>If your payment is still confirming, we'll email/deliver "
        "the moment it lands. You can close this page.</p>"
    )


# ---------------------------------------------------------------------------
#  Webhook — the security-critical part. VERIFY before you trust.
# ---------------------------------------------------------------------------
@app.post("/webhook")
def webhook():
    """The gateway POSTs a JSON body plus an `X-OPG-Signature` header. Both the header
    and the body's `signature` field are HMAC-SHA256 over the canonical payload.

    We recompute the signature over the received body with our shared secret and
    compare in constant time. sign_params() deliberately EXCLUDES the `signature`
    field (and any empty values) from what it signs — so we can hand it the whole
    payload as-is. Reject anything that doesn't match BEFORE touching the order.
    """
    payload = request.get_json(force=True, silent=True) or {}

    given = request.headers.get("X-OPG-Signature") or str(payload.get("signature") or "")
    expected = sign_params(WEBHOOK_SECRET, payload)
    if not constant_time_equals(given, expected):
        log.warning("rejected webhook with bad signature for %s", payload.get("trade_id"))
        abort(401, "bad signature")

    trade_id = payload.get("trade_id")
    if payload.get("status") == "paid":
        rec = ORDERS.setdefault(trade_id, {})
        if rec.get("status") != "paid":          # idempotent: fulfil at most once
            rec["status"] = "paid"
            rec["received_cents"] = payload.get("received_cents")
            log.info("order %s PAID (%s) — fulfilling", trade_id, payload.get("amount"))
            # TODO: fulfil here (grant access, send the download, email a key, …).
    # Any 2xx tells the gateway the callback was accepted; non-2xx => it retries.
    return {"ok": True}


@app.get("/order/<trade_id>")
def order_status(trade_id: str):
    # Live status straight from the gateway (source of truth), with our local view.
    order = get_payment(trade_id)
    if not order:
        abort(404)
    local = ORDERS.get(trade_id, {})
    return {
        "trade_id": trade_id,
        "status": order["status"],
        "received_cents": order["received_cents"],
        "expected_cents": order["pay_amount_cents"],
        "local": local,
    }


def main() -> None:
    # To detect payments from THIS process, uncomment the workers:
    #     from server import workers; workers.start_background()
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True)


if __name__ == "__main__":
    main()
