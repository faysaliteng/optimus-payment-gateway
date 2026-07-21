"""
Binance verification — confirm a customer's payment landed in YOUR OWN Binance
account, using a personal, READ-ONLY API key. This is the "second rail" alongside
the on-chain gateways: a buyer pays you via **Binance Pay** (P2P/merchant) or an
on-chain **deposit to Binance**, submits their order id / txid, and you verify it
against your real Binance history before crediting.

Two reference types:
  * a numeric **Binance Pay order id**  -> GET /sapi/v1/pay/transactions
  * an on-chain **deposit txid**        -> GET /sapi/v1/capital/deposit/hisrec

All requests are HMAC-SHA256 signed (Binance's standard signed-endpoint scheme:
sign the query string with your API secret, send `X-MBX-APIKEY`). The key is
READ-ONLY — this module NEVER holds withdrawal permission.

WHAT MAKES A MATCH (all must hold):
  1. the reference appears in your history (checked across every id field Binance
     uses: transactionId / orderId / merchantTradeNo / prepayId / bizId / tradeNo),
  2. the transaction STATUS is a success state,
  3. the AMOUNT matches (exact, 2-dp floor, or within a small autocorrect delta;
     non-stable assets are converted to USDT via the spot price),
  4. the payment went to **your** Pay ID / deposit address (receiver match) — so a
     buyer can't submit a real order id that paid *someone else*,
  5. (optional) the payment is at least `min_age_minutes` old (anti-race).

MULTI-ACCOUNT: `BinanceVerifier(BinanceAccount(...))` is parameterised by one set
of credentials, so the SAME code verifies for the platform account AND for each
seller's own account (per-seller "Direct Payment" mode) — fully isolated.

REPLAY SAFETY: verification alone is NOT enough. A verified reference must be
BURNED in the reference registry (`optimus_gateway.db`) BEFORE crediting, so a
retry/re-submit/replay can never double-credit. `verify_and_claim()` does both
atomically. See docs/BINANCE.md and docs/SECURITY.md.
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
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

from .config import config
from .security import normalize_reference

log = logging.getLogger("optimus_gateway.binance")

# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------
_ORDER_ID_RE = re.compile(r"^\d{8,40}$")
PAY_OK_STATUSES = {"PAID", "PAY_SUCCESS", "SUCCESS", "COMPLETED", "FINISHED"}
DEPOSIT_OK_STATUSES = {1, 6}  # 1 = success, 6 = credited but locked
STABLE = {"USDT", "USDC", "BUSD", "FDUSD"}
# Absolute USDT delta we will still accept / auto-correct to (covers a buyer who
# eyeballs "$4.00" and sends 3.99, or a tiny fee/rounding difference). Distinct
# from the wider per-account `amount_tolerance` used for spot-converted assets.
AUTOCORRECT_MAX_DELTA = 0.02

_USER_AGENT = "optimus-payment-gateway/binance-verify"


# ---------------------------------------------------------------------------
#  Pure helpers (no network) — every field-matching rule lives here so it is
#  independently unit-testable against captured Binance responses.
# ---------------------------------------------------------------------------
def reference_candidates(tx: dict) -> set[str]:
    """Every normalized reference a Binance Pay row could be matched on. Binance
    returns the id under different keys depending on the flow (P2P vs merchant vs
    order) — check them all so a real payment is never missed."""
    if not isinstance(tx, dict):
        return set()
    out = {
        normalize_reference(tx.get("transactionId")),
        normalize_reference(tx.get("orderId")),
        normalize_reference(tx.get("merchantTradeNo")),
        normalize_reference(tx.get("prepayId")),
        normalize_reference(tx.get("bizId")),
        normalize_reference(tx.get("tradeNo")),
        normalize_reference(tx.get("note")),
    }
    return {v for v in out if v}


def status_ok(tx: dict) -> bool:
    """True if the row's status is a success state (or absent — some P2P rows omit
    it and are already settled when they appear in history)."""
    status = str(
        tx.get("status") or tx.get("transactionStatus")
        or tx.get("orderStatus") or tx.get("bizStatus") or ""
    ).strip().upper()
    return (not status) or status in PAY_OK_STATUSES


def _flatten_strings(value) -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for v in value.values():
            out.extend(_flatten_strings(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_flatten_strings(v))
    elif value is not None:
        out.append(str(value).strip())
    return [x for x in out if x]


def _floor2(value) -> float | None:
    try:
        return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
    except Exception:  # noqa: BLE001
        return None


def amount_pairs(tx: dict) -> list[tuple[float, str]]:
    """All (amount, currency) pairs a row exposes — top-level fields plus every
    entry in `fundsDetail`. We accept a match on any of them."""
    pairs: list[tuple[float, str]] = []
    for amt_key, cur_key in (
        ("amount", "currency"), ("totalFee", "currency"),
        ("transactionAmount", "currency"), ("sourceAmount", "sourceCurrency"),
    ):
        raw = tx.get(amt_key)
        if raw is None:
            continue
        try:
            pairs.append((abs(float(raw)), str(tx.get(cur_key) or "").strip().upper()))
        except (TypeError, ValueError):
            continue
    fd = tx.get("fundsDetail")
    if isinstance(fd, list):
        for item in fd:
            if not isinstance(item, dict):
                continue
            try:
                pairs.append((abs(float(item.get("amount"))),
                              str(item.get("currency") or "").strip().upper()))
            except (TypeError, ValueError):
                continue
    return pairs


def amount_matches(tx: dict, expected_amount: float | None,
                   expected_currency: str | None = None,
                   max_delta: float = AUTOCORRECT_MAX_DELTA) -> bool:
    """True if any of the row's amounts matches `expected_amount` — exact, 2-dp
    floor, or within `max_delta`. Currency is matched when both sides declare one."""
    if expected_amount is None:
        return True
    expected_floor = _floor2(expected_amount)
    if expected_floor is None:
        return False
    cur = (expected_currency or "").strip().upper()
    for amount_value, currency_value in amount_pairs(tx):
        if cur and currency_value and currency_value != cur:
            continue
        try:
            exact = abs(float(amount_value) - float(expected_amount)) <= 1e-7
        except (TypeError, ValueError):
            exact = False
        floored = _floor2(amount_value)
        two_dp = floored is not None and abs(floored - expected_floor) <= 1e-7
        within = floored is not None and abs(floored - expected_floor) <= max_delta
        if exact or two_dp or within:
            return True
    return False


def autocorrect_amount(tx: dict, expected_amount: float | None,
                       expected_currency: str | None = None,
                       max_delta: float = AUTOCORRECT_MAX_DELTA) -> tuple[float | None, str, float | None]:
    """The closest matching amount within `max_delta` -> (amount, currency, delta),
    or (None,"",None). Lets you credit the amount ACTUALLY received rather than the
    slightly-off amount the buyer expected."""
    if not isinstance(tx, dict) or expected_amount is None:
        return None, "", None
    expected_floor = _floor2(expected_amount)
    if expected_floor is None:
        return None, "", None
    cur = (expected_currency or "").strip().upper()
    best: tuple[float, str, float] | None = None
    for amount_value, currency_value in amount_pairs(tx):
        if cur and currency_value and currency_value != cur:
            continue
        floored = _floor2(amount_value)
        if floored is None:
            continue
        delta = abs(floored - expected_floor)
        if delta > max_delta:
            continue
        if best is None or delta < best[2]:
            best = (floored, currency_value or cur, delta)
    return best if best is not None else (None, "", None)


def destination_matches(tx: dict, expected_receiver: str) -> bool:
    """True if the payment landed on YOUR receiver (Pay ID). Empty receiver = skip
    the check. This is the guard that stops a buyer submitting a real order id that
    actually paid a DIFFERENT merchant."""
    receiver = str(expected_receiver or "").strip()
    if not receiver:
        return True
    keys = {
        str(tx.get("receiverBinanceId") or "").strip(),
        str(tx.get("receiverId") or "").strip(),
        str(tx.get("payeeId") or "").strip(),
        str(tx.get("binancePayId") or "").strip(),
    }
    info = tx.get("receiverInfo")
    if isinstance(info, dict):
        keys.update(str(v).strip() for v in info.values() if v is not None)
    keys = {k for k in keys if k}
    return (receiver in keys) if keys else (receiver in _flatten_strings(tx))


def row_time_ms(row: dict) -> int | None:
    """Best-effort epoch-ms for a Pay row (used by paging + the min-age guard)."""
    for key in ("transactionTime", "payTime", "createTime", "updateTime", "time"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            ts = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if ts <= 0:
            continue
        return ts * 1000 if ts < 10 ** 12 else ts
    return None


# ---------------------------------------------------------------------------
#  Account + verifier
# ---------------------------------------------------------------------------
@dataclass
class BinanceAccount:
    """One read-only Binance account. Use one per platform, or one per seller for
    isolated "Direct Payment" mode."""
    api_key: str
    api_secret: str
    base_url: str = "https://api.binance.com"
    pay_id: str = ""                 # your Binance Pay ID (receiver). Strongly recommended.
    amount_tolerance: float = 0.50   # wider band for spot-converted (non-stable) assets
    min_age_minutes: int = 0         # reject payments younger than this (anti-race)
    label: str = "platform"

    @classmethod
    def from_config(cls) -> "BinanceAccount":
        return cls(
            api_key=config.BINANCE_API_KEY,
            api_secret=config.BINANCE_API_SECRET,
            base_url=config.BINANCE_BASE_URL,
            pay_id=getattr(config, "BINANCE_PAY_ID", "") or "",
            amount_tolerance=config.BINANCE_AMOUNT_TOLERANCE,
            min_age_minutes=int(getattr(config, "BINANCE_MIN_AGE_MINUTES", 0) or 0),
        )

    def enabled(self) -> bool:
        return bool(self.api_key and self.api_secret)


class BinanceVerifier:
    """Verify payments against ONE Binance account. All methods are best-effort on
    the network layer (never raise on a failed HTTP call — return a reason)."""

    def __init__(self, account: BinanceAccount):
        self.account = account

    # -- signed transport -------------------------------------------------
    def _signed_get(self, path: str, params: dict, timeout: int = 20):
        acc = self.account
        full = dict(params or {})
        full.setdefault("timestamp", int(time.time() * 1000))
        full.setdefault("recvWindow", 10_000)
        query = urllib.parse.urlencode(full, doseq=True)
        sig = hmac.new(acc.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{acc.base_url.rstrip('/')}{path}?{query}&signature={sig}"
        req = urllib.request.Request(url, headers={
            "X-MBX-APIKEY": acc.api_key, "User-Agent": _USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return e.code, str(e)
        except Exception as e:  # noqa: BLE001
            return 0, "network error: %s" % e

    @staticmethod
    def _json(body: str):
        try:
            return json.loads(body) if body else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _binance_error(body: str) -> str:
        j = BinanceVerifier._json(body)
        if isinstance(j, dict) and (j.get("code") or j.get("msg")):
            return "Binance error %s: %s" % (j.get("code"), j.get("msg"))
        return str(body)[:200]

    # -- connection test --------------------------------------------------
    def test_connection(self) -> dict:
        """Validate creds + IP allow-list via /api/v3/account. Never trades."""
        if not self.account.enabled():
            return {"ok": False, "reason": "no_credentials", "message": "API key/secret missing."}
        st, body = self._signed_get("/api/v3/account", {})
        if st == 200:
            data = self._json(body) or {}
            return {"ok": True, "account_type": data.get("accountType") or "SPOT",
                    "can_trade": bool(data.get("canTrade")), "raw_status": st}
        return {"ok": False, "reason": "api_error", "raw_status": st,
                "message": self._binance_error(body)}

    # -- Binance Pay history ---------------------------------------------
    def fetch_pay_history_window(self, start_ms: int, end_ms: int, limit: int = 100) -> list[dict]:
        st, body = self._signed_get("/sapi/v1/pay/transactions",
                                    {"startTime": int(start_ms), "endTime": int(end_ms),
                                     "limit": max(1, min(int(limit), 100))})
        if st != 200:
            log.warning("pay/transactions window failed (%s): %s", st, self._binance_error(body))
            return []
        j = self._json(body)
        rows = j.get("data") if isinstance(j, dict) else j
        return rows if isinstance(rows, list) else []

    def fetch_pay_history(self, days: int = 90, limit: int = 100) -> list[dict]:
        now = int(time.time() * 1000)
        return self.fetch_pay_history_window(now - int(days) * 86_400_000, now, limit=limit)

    def fetch_pay_history_deep(self, lookback_days: int = 548, stop_reference: str = "",
                               limit: int = 100) -> list[dict]:
        """Page back through history in 90-day windows (Binance's max), newest
        first, up to ~18 months. Stops early once `stop_reference` is found. The
        endpoint has a high UID weight — reserve this for manual/admin checks."""
        rows: list[dict] = []
        now = int(time.time() * 1000)
        window = 89 * 86_400_000
        earliest = now - max(1, int(lookback_days)) * 86_400_000
        target = normalize_reference(stop_reference)
        seen: set[str] = set()
        calls = 0
        end = now
        while end > earliest and calls < 120:
            start = max(earliest, end - window)
            cursor_end = end
            while cursor_end >= start and calls < 120:
                batch = self.fetch_pay_history_window(start, cursor_end, limit=limit)
                calls += 1
                if not batch:
                    break
                oldest = None
                for row in batch:
                    refs = reference_candidates(row)
                    key = "|".join(sorted(refs)) or json.dumps(row, sort_keys=True, default=str)[:300]
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(row)
                    if target and target in refs:
                        return rows
                    ts = row_time_ms(row)
                    if ts is not None:
                        oldest = ts if oldest is None else min(oldest, ts)
                if len(batch) < int(limit) or oldest is None or oldest <= start:
                    break
                nxt = oldest - 1
                if nxt >= cursor_end:
                    break
                cursor_end = nxt
            end = start - 1
        return rows

    # -- the matcher ------------------------------------------------------
    def find_pay_transaction(self, reference: str, expected_amount: float | None = None,
                             expected_currency: str | None = None,
                             expected_receiver: str | None = None,
                             deep: bool = False) -> dict | None:
        """Return the FIRST history row that matches the reference AND passes the
        status / amount / receiver checks, or None."""
        ref = normalize_reference(reference)
        if not ref or len(ref) < 6:
            return None
        receiver = expected_receiver if expected_receiver is not None else self.account.pay_id
        rows = (self.fetch_pay_history_deep(stop_reference=ref)
                if deep else self.fetch_pay_history())
        for tx in rows:
            if not isinstance(tx, dict) or ref not in reference_candidates(tx):
                continue
            if not status_ok(tx):
                continue
            if not amount_matches(tx, expected_amount, expected_currency,
                                  max_delta=max(AUTOCORRECT_MAX_DELTA, self.account.amount_tolerance)):
                continue
            if not destination_matches(tx, receiver or ""):
                continue
            return tx
        return None

    # -- the public verification call ------------------------------------
    def verify_pay_reference(self, reference: str, expected_amount: float,
                             expected_currency: str = "USDT",
                             expected_receiver: str | None = None,
                             min_age_minutes: int | None = None,
                             deep: bool = False) -> dict:
        """Verify a customer-submitted Binance Pay order id paid YOU `expected_amount`.
        Returns {ok, reason?, amount?, currency?, received_at?, reference, raw?}.

        Does NOT touch the reference registry — call `verify_and_claim` (or claim the
        reference yourself) BEFORE crediting so it can't be replayed."""
        acc = self.account
        if not acc.enabled():
            return {"ok": False, "reason": "no_credentials"}
        ref = normalize_reference(reference)
        if not ref:
            return {"ok": False, "reason": "missing_reference"}
        if not _ORDER_ID_RE.match(ref):
            return {"ok": False, "reason": "not_a_pay_order_id", "reference": ref}
        tx = self.find_pay_transaction(ref, expected_amount, expected_currency,
                                       expected_receiver, deep=deep)
        if tx is None:
            # Retry once with a deep scan before giving up (old reference).
            if not deep:
                tx = self.find_pay_transaction(ref, expected_amount, expected_currency,
                                               expected_receiver, deep=True)
            if tx is None:
                return {"ok": False, "reason": "not_found", "reference": ref}
        # amount actually received (auto-corrected to the matched value)
        amount, currency, _delta = autocorrect_amount(
            tx, expected_amount, expected_currency,
            max_delta=max(AUTOCORRECT_MAX_DELTA, acc.amount_tolerance))
        if amount is None:
            pairs = amount_pairs(tx)
            amount = pairs[0][0] if pairs else 0.0
            currency = (pairs[0][1] if pairs else expected_currency) or "USDT"
        # min-age guard
        age_floor = acc.min_age_minutes if min_age_minutes is None else int(min_age_minutes)
        recv_ms = row_time_ms(tx) or 0
        if age_floor > 0 and recv_ms:
            age_sec = (time.time() * 1000 - recv_ms) / 1000.0
            if age_sec < age_floor * 60:
                return {"ok": False, "reason": "too_recent",
                        "message": "payment is only %ds old (min %d min)" % (int(age_sec), age_floor),
                        "reference": ref}
        return {"ok": True, "amount": round(float(amount), 2),
                "currency": (currency or "USDT").upper(),
                "received_at": recv_ms, "reference": ref, "raw": tx}

    # -- on-chain deposit TO Binance -------------------------------------
    def fetch_deposit_history(self, days: int = 90, limit: int = 1000) -> list[dict]:
        now = int(time.time() * 1000)
        st, body = self._signed_get("/sapi/v1/capital/deposit/hisrec",
                                    {"startTime": now - int(days) * 86_400_000,
                                     "endTime": now, "limit": int(limit)})
        if st != 200:
            log.warning("capital/deposit/hisrec failed (%s): %s", st, self._binance_error(body))
            return []
        rows = self._json(body)
        return rows if isinstance(rows, list) else []

    def verify_deposit_txid(self, txid: str, expected_amount: float,
                            expected_address: str = "") -> dict:
        """Confirm an on-chain deposit txid credited YOUR Binance account for
        ~expected_amount (converted to USDT for non-stable coins)."""
        if not self.account.enabled():
            return {"ok": False, "reason": "no_credentials"}
        norm = str(txid or "").strip().lower()
        if not norm:
            return {"ok": False, "reason": "missing_txid"}
        for row in self.fetch_deposit_history():
            if str(row.get("txId") or "").strip().lower() != norm:
                continue
            if int(row.get("status", -1)) not in DEPOSIT_OK_STATUSES:
                return {"ok": False, "reason": "pending_confirmations", "txid": norm}
            if expected_address and str(row.get("address") or "").lower() != expected_address.lower():
                return {"ok": False, "reason": "address_mismatch", "txid": norm}
            asset = str(row.get("coin") or "USDT").upper()
            usdt = spot_to_usdt(asset, abs(float(row.get("amount") or 0)), self.account.base_url)
            if abs((_floor2(usdt) or 0) - (_floor2(expected_amount) or 0)) <= self.account.amount_tolerance:
                return {"ok": True, "amount": round(usdt, 2), "asset": asset, "txid": norm, "raw": row}
            return {"ok": False, "reason": "amount_mismatch", "actual": round(usdt, 2), "txid": norm}
        return {"ok": False, "reason": "not_found", "txid": norm}

    # -- verify + burn (the replay-safe path) ----------------------------
    def verify_and_claim(self, reference: str, expected_amount: float, **kwargs) -> dict:
        """Verify a Binance Pay reference AND burn it into the reference registry in
        one step. Returns ok=True ONLY if the payment verified AND the reference was
        NOT previously used. A replay (same reference again) returns
        {ok:False, reason:'already_used'} — the credit must not be applied.

        This is the safe call to gate crediting on."""
        result = self.verify_pay_reference(reference, expected_amount, **kwargs)
        if not result.get("ok"):
            return result
        try:
            from . import db  # local import: keep verification usable without the DB
            claimed = db.claim_reference(result["reference"], reference_type="binance_pay")
        except Exception as exc:  # noqa: BLE001
            log.error("reference registry unavailable: %s", exc)
            return {"ok": False, "reason": "registry_unavailable", "reference": result["reference"]}
        if not claimed:
            return {"ok": False, "reason": "already_used", "reference": result["reference"]}
        return result


# ---------------------------------------------------------------------------
#  Spot conversion (non-stable asset -> USDT)
# ---------------------------------------------------------------------------
def spot_to_usdt(asset: str, amount: float, base_url: str = "https://api.binance.com") -> float:
    asset = str(asset or "").upper()
    if asset in STABLE:
        return float(amount)
    sym = ("MATIC" if asset == "POL" else asset) + "USDT"
    try:
        url = f"{base_url.rstrip('/')}/api/v3/ticker/price?symbol={sym}"
        with urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": _USER_AGENT}), timeout=10) as r:
            price = float(json.load(r).get("price") or 0)
        return float(amount) * price
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
#  Binance Pay MERCHANT webhook (callback) verification
#  ---------------------------------------------------------------------------
#  If you use Binance Pay's merchant API, Binance POSTs an order-status callback
#  signed HMAC-SHA512 over "timestamp\nnonce\nbody\n". FAIL-CLOSED: with no secret
#  we reject (a missing secret must never let a forged "paid" webhook credit).
# ---------------------------------------------------------------------------
def verify_pay_webhook(raw_body: bytes, timestamp: str, nonce: str,
                       signature: str, secret: str) -> bool:
    secret = (secret or "").strip()
    if not secret or not timestamp or not nonce or not signature:
        return False
    try:
        body_str = raw_body.decode("utf-8") if raw_body else ""
    except Exception:  # noqa: BLE001
        return False
    payload = f"{timestamp}\n{nonce}\n{body_str}\n"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha512).hexdigest().upper()
    return hmac.compare_digest(str(signature).upper(), expected)


def webhook_status(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return str(data.get("status") or payload.get("bizStatus") or payload.get("status") or "").strip().upper()


def webhook_trade_no(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return str(data.get("merchantTradeNo") or payload.get("merchantTradeNo") or "").strip()


# ---------------------------------------------------------------------------
#  Backwards-compatible module-level API (uses the default account from config)
# ---------------------------------------------------------------------------
def _default_verifier() -> BinanceVerifier:
    return BinanceVerifier(BinanceAccount.from_config())


def enabled() -> bool:
    return bool(config.BINANCE_ENABLED and config.BINANCE_API_KEY and config.BINANCE_API_SECRET)


def verify_binance_pay(reference: str, expected_amount: float,
                       expected_receiver: str | None = None) -> dict:
    """Back-compat wrapper. Verifies against the account from OPG_BINANCE_* env.
    Returns {ok, credited_usdt?, reason?}."""
    if not enabled():
        return {"ok": False, "reason": "binance_disabled"}
    res = _default_verifier().verify_pay_reference(
        reference, expected_amount, expected_receiver=expected_receiver)
    if res.get("ok"):
        return {"ok": True, "credited_usdt": res["amount"], "asset": res["currency"],
                "reference": res["reference"]}
    return res


def verify_deposit_txid(txid: str, expected_amount: float, expected_address: str = "") -> dict:
    if not enabled():
        return {"ok": False, "reason": "binance_disabled"}
    res = _default_verifier().verify_deposit_txid(txid, expected_amount, expected_address)
    if res.get("ok"):
        return {"ok": True, "credited_usdt": res["amount"], "asset": res["asset"], "txid": res["txid"]}
    return res
