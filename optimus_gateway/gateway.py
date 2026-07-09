"""
High-level gateway API — what your bot / web app / the REST server calls.

    order = create_payment("usdt_bep20", 25.00, merchant_order_id="INV-1001",
                           notify_url="https://shop/webhook")
    # -> {trade_id, pay_address, pay_amount, pay_amount_cents, method, expires_at, ...}
    # show order["pay_address"] + order["pay_amount"] (+ a QR) to the payer.

    status = get_payment(order["trade_id"])     # poll, or rely on the webhook

Which attribution mode is used is chosen automatically:
  * TON method  -> per-order MEMO on your shared TON address.
  * EVM + XPUB  -> a fresh per-order ADDRESS (watch-only HD derivation). RECOMMENDED.
  * EVM + shared address only -> unique-AMOUNT matching on your shared address.
"""
from __future__ import annotations

import secrets
import string

from . import db, hdwallet
from .chains import CHAINS, is_evm
from .config import config

_MEMO_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 (no I L O U)


def _new_memo() -> str:
    return "OPG" + "".join(secrets.choice(_MEMO_ALPHABET) for _ in range(13))  # ~65 bits


def _payment_uri(method: str, address: str, amount: str, memo: str = "") -> str:
    """A wallet deep-link / QR payload for the payer."""
    cfg = CHAINS[method]
    if method == "usdt_ton":
        base = f"ton://transfer/{address}?amount=&text={memo}"
        return base
    # EIP-681-ish token transfer link (many wallets accept a plain address too)
    return address


def create_payment(method: str, amount: float, *, merchant_order_id=None,
                   notify_url=None, redirect_url=None, metadata=None,
                   ttl_minutes=None) -> dict:
    """Reserve a payment and return everything the payer needs."""
    if method not in CHAINS:
        raise ValueError(f"unknown method {method!r}")
    if method not in config.ENABLED_METHODS:
        raise ValueError(f"method {method!r} is not enabled")

    pay_address = None
    address_index = None
    pay_memo = None
    amount_match = False

    if method == "usdt_ton":
        pay_address = config.TON_RECEIVE_ADDRESS
        if not pay_address:
            raise RuntimeError("OPG_TON_ADDRESS not configured")
        pay_memo = _new_memo()
    elif is_evm(method) and config.GATEWAY_XPUB:
        # per-order address (recommended): derive a fresh child of the watch-only xpub.
        # A single GLOBAL index across all EVM methods keeps every address unique, so
        # BSC/ETH/Polygon orders can never collide on the same address.
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            address_index = db.next_address_index(conn, "_evm")
            conn.commit()
        finally:
            conn.close()
        pay_address = hdwallet.address_from_xpub(config.GATEWAY_XPUB, address_index)
    elif is_evm(method) and config.SHARED_RECEIVE_ADDRESS:
        # amount-match mode on a shared address
        pay_address = config.SHARED_RECEIVE_ADDRESS
        amount_match = True
    else:
        raise RuntimeError(
            "Configure OPG_GATEWAY_XPUB (per-order addresses, recommended) or "
            "OPG_SHARED_RECEIVE_ADDRESS (amount-match) for EVM methods")

    order = db.create_order(
        method, amount, merchant_order_id=merchant_order_id, notify_url=notify_url,
        redirect_url=redirect_url, metadata=metadata, pay_address=pay_address,
        pay_memo=pay_memo, address_index=address_index, amount_match=amount_match,
        ttl_minutes=ttl_minutes)
    return _public_order(order)


def _public_order(order: dict) -> dict:
    method = order["method"]
    cfg = CHAINS[method]
    cents = int(order["expected_cents"])
    amount_str = f"{cents / 100:.2f}"
    return {
        "trade_id": order["trade_id"],
        "merchant_order_id": order.get("merchant_order_id"),
        "method": method,
        "network": cfg["label"],
        "token": "USDT",
        "quote_amount": order.get("quote_amount"),
        "quote_currency": order.get("quote_currency"),
        "pay_amount": amount_str,
        "pay_amount_cents": cents,
        "received_cents": int(order.get("received_cents") or 0),
        "pay_address": order.get("pay_address"),
        "pay_memo": order.get("pay_memo"),
        "payment_uri": _payment_uri(method, order.get("pay_address") or "", amount_str,
                                    order.get("pay_memo") or ""),
        "status": order["status"],
        "expires_at": order.get("reservation_expires_at"),
        "checkout_url": f"{config.BASE_URL}/pay/{order['trade_id']}",
        "explorer": cfg.get("explorer"),
        "created_at": order.get("created_at"),
        "paid_at": order.get("paid_at"),
    }


def get_payment(trade_id: str) -> dict | None:
    order = db.get_order(trade_id=trade_id)
    return _public_order(order) if order else None
