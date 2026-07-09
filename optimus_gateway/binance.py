"""
Binance verification (optional) — verify a payment landed in YOUR OWN Binance account
using a personal, READ-ONLY API key. Two reference types:

  * a numeric Binance Pay Order ID   -> GET /sapi/v1/pay/transactions
  * an on-chain deposit txid         -> GET /sapi/v1/capital/deposit/hisrec

Requests are HMAC-SHA256 signed (Binance's standard signed-endpoint scheme: sign the
query string with your API secret, send X-MBX-APIKEY). Non-stable assets (BTC/ETH/…)
are converted to USDT via the public spot price. A match requires the reference +
amount (within a tolerance) + a success status. The caller then burns the reference
in the registry so it can't be replayed.

This complements the on-chain gateways: use it if you also accept Binance Pay, or want
a second source of truth. It NEVER holds withdrawal permission — read-only key only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import urllib.parse
import urllib.request

from .config import config

log = logging.getLogger("optimus_gateway.binance")

_ORDER_ID_RE = re.compile(r"^\d{10,30}$")
_PAY_OK = {"PAID", "PAY_SUCCESS", "SUCCESS", "COMPLETED", "FINISHED"}
_DEPOSIT_OK = {1, 6}  # 1=success, 6=credited but locked
STABLE = {"USDT", "USDC", "BUSD", "FDUSD"}
NATIVE = {"BTC", "ETH", "LTC", "BNB", "POL", "MATIC", "TON", "TRX", "SOL"}


def enabled() -> bool:
    return bool(config.BINANCE_ENABLED and config.BINANCE_API_KEY and config.BINANCE_API_SECRET)


def _signed_get(path: str, params: dict) -> dict | list | None:
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    query = urllib.parse.urlencode(params, doseq=True)
    sig = hmac.new(config.BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{config.BINANCE_BASE_URL}{path}?{query}&signature={sig}"
    try:
        req = urllib.request.Request(url, headers={"X-MBX-APIKEY": config.BINANCE_API_KEY})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception as exc:  # noqa: BLE001
        log.warning("binance GET %s failed: %s", path, exc)
        return None


def _spot_to_usdt(asset: str, amount: float) -> float:
    asset = asset.upper()
    if asset in STABLE:
        return amount
    sym = ("MATIC" if asset == "POL" else asset) + "USDT"
    try:
        url = f"{config.BINANCE_BASE_URL}/api/v3/ticker/price?symbol={sym}"
        with urllib.request.urlopen(url, timeout=10) as r:
            price = float(json.load(r).get("price") or 0)
        return amount * price
    except Exception:  # noqa: BLE001
        return 0.0


def _floor2(x: float) -> float:
    return int(float(x) * 100) / 100.0


def _amount_ok(actual_usdt: float, expected: float) -> bool:
    if _floor2(actual_usdt) == _floor2(expected):
        return True
    return abs(_floor2(actual_usdt) - _floor2(expected)) <= config.BINANCE_AMOUNT_TOLERANCE


def verify_binance_pay(reference: str, expected_amount: float) -> dict:
    """Confirm a Binance Pay Order ID paid you ~expected_amount. Returns
    {ok, credited_usdt?, reason?}."""
    if not enabled():
        return {"ok": False, "reason": "binance_disabled"}
    ref = str(reference or "").strip()
    if not _ORDER_ID_RE.match(ref):
        return {"ok": False, "reason": "not_a_pay_order_id"}
    data = _signed_get("/sapi/v1/pay/transactions", {"limit": 100})
    rows = (data or {}).get("data") if isinstance(data, dict) else None
    for row in (rows or []):
        ids = {str(row.get(k)) for k in ("orderId", "transactionId", "merchantTradeNo",
                                         "prepayId", "bizId", "tradeNo")}
        if ref not in ids:
            continue
        if str(row.get("transactionStatus") or row.get("status") or "").upper() not in _PAY_OK:
            return {"ok": False, "reason": "not_completed"}
        asset = str(row.get("currency") or row.get("asset") or "USDT").upper()
        amount = abs(float(row.get("amount") or 0))
        usdt = _spot_to_usdt(asset, amount)
        if _amount_ok(usdt, expected_amount):
            return {"ok": True, "credited_usdt": round(usdt, 2), "asset": asset, "reference": ref}
        return {"ok": False, "reason": "amount_mismatch", "actual": round(usdt, 2)}
    return {"ok": False, "reason": "not_found"}


def verify_deposit_txid(txid: str, expected_amount: float, expected_address: str = "") -> dict:
    """Confirm an on-chain deposit txid credited your Binance account. Returns
    {ok, credited_usdt?, reason?}."""
    if not enabled():
        return {"ok": False, "reason": "binance_disabled"}
    norm = str(txid or "").strip().lower()
    end = int(time.time() * 1000)
    start = end - 90 * 24 * 3600 * 1000
    data = _signed_get("/sapi/v1/capital/deposit/hisrec",
                       {"startTime": start, "endTime": end, "limit": 1000})
    for row in (data or []):
        if str(row.get("txId") or "").strip().lower() != norm:
            continue
        if int(row.get("status", -1)) not in _DEPOSIT_OK:
            return {"ok": False, "reason": "binance_pending_confirmations"}
        if expected_address and str(row.get("address") or "").lower() != expected_address.lower():
            return {"ok": False, "reason": "address_mismatch"}
        asset = str(row.get("coin") or "USDT").upper()
        amount = abs(float(row.get("amount") or 0))
        usdt = _spot_to_usdt(asset, amount)
        if _amount_ok(usdt, expected_amount):
            return {"ok": True, "credited_usdt": round(usdt, 2), "asset": asset, "txid": norm}
        return {"ok": False, "reason": "amount_mismatch", "actual": round(usdt, 2)}
    return {"ok": False, "reason": "not_found"}
