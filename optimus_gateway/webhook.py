"""
Webhook delivery — signed, queued, retried server-to-server callbacks so the merchant
learns about a payment even if they weren't polling.

On payment we enqueue a callback; a background loop POSTs it to the order's notify_url
with an HMAC-SHA256 signature (header X-OPG-Signature + a `signature` field). Failures
retry with exponential backoff up to WEBHOOK_MAX_RETRIES. The merchant verifies the
signature with their shared secret — identical scheme to how they signed create-order.
"""
from __future__ import annotations

import json
import logging
import urllib.request

from . import db
from .config import config
from .security import sign_webhook, new_timestamp_ms

log = logging.getLogger("optimus_gateway.webhook")


def build_payload(order: dict) -> dict:
    cents = int(order["expected_cents"])
    payload = {
        "event": "payment.completed" if order["status"] == "paid" else "payment.updated",
        "trade_id": order["trade_id"],
        "merchant_order_id": order.get("merchant_order_id"),
        "method": order["method"],
        "status": order["status"],
        "amount": f"{cents / 100:.2f}",
        "amount_cents": cents,
        "received_cents": int(order.get("received_cents") or 0),
        "pay_address": order.get("pay_address"),
        "tx_hashes": order.get("tx_hashes") or "",
        "timestamp": new_timestamp_ms(),
    }
    payload["signature"] = sign_webhook(config.MERCHANT_API_SECRET, payload)
    return payload


def on_paid(order: dict) -> None:
    """Callback wired into the watcher: enqueue a webhook when an order is paid."""
    if order.get("notify_url"):
        db.enqueue_webhook(int(order["id"]), order["notify_url"], build_payload(order))
        log.info("queued webhook for order %s -> %s", order["trade_id"], order["notify_url"])


def deliver_due(limit: int = 20) -> int:
    """Send all due webhooks. Returns how many were delivered."""
    delivered = 0
    for wh in db.due_webhooks(limit=limit):
        body = json.dumps(json.loads(wh["payload"])).encode()
        try:
            req = urllib.request.Request(
                wh["url"], data=body,
                headers={"Content-Type": "application/json",
                         "X-OPG-Signature": json.loads(wh["payload"]).get("signature", ""),
                         "User-Agent": "OptimusGateway/1.0"})
            with urllib.request.urlopen(req, timeout=config.WEBHOOK_TIMEOUT) as r:
                code = r.getcode()
            if 200 <= code < 300:
                db.mark_webhook(wh["id"], delivered=True)
                delivered += 1
            else:
                db.mark_webhook(wh["id"], error=f"HTTP {code}",
                                retry_delay=_backoff(wh["attempts"]),
                                max_retries=config.WEBHOOK_MAX_RETRIES)
        except Exception as exc:  # noqa: BLE001
            db.mark_webhook(wh["id"], error=str(exc), retry_delay=_backoff(wh["attempts"]),
                            max_retries=config.WEBHOOK_MAX_RETRIES)
    return delivered


def _backoff(attempts: int) -> int:
    return min(3600, 30 * (2 ** int(attempts or 0)))
