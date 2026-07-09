"""
Security primitives shared across the gateway:

  * payment-reference normalization + the anti-replay idempotency key,
  * HMAC-SHA256 signing for the merchant API (request auth) and outbound webhooks,
  * constant-time comparison.

The golden rule of this gateway: an on-chain txid (or a Binance reference) is
"burned" into the payment_reference_registry BEFORE any balance is credited, so a
re-scan / retry / replay can never double-credit. The helpers here produce the
normalized key that table is keyed on.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def normalize_reference(reference: str) -> str:
    """Canonicalise a payment reference (txid / order id) for the replay lock.

    Upper-cases, strips whitespace/backticks/quotes, and picks the longest token
    that contains a digit — so '0xABC...'/'ABC...' and copy-paste noise collapse to
    the same key. Empty string means "no usable reference".
    """
    if not reference:
        return ""
    s = str(reference).strip().strip("`'\"").replace("\n", " ")
    toks = [t for t in _TOKEN_RE.findall(s) if any(ch.isdigit() for ch in t)]
    if not toks:
        toks = _TOKEN_RE.findall(s)
    if not toks:
        return ""
    return max(toks, key=len).upper()


def hmac_sha256(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a or ""), str(b or ""))


# ---------------------------------------------------------------------------
#  Merchant API request signing (create-order etc.)
#  ---------------------------------------------------------------------------
#  Merchants sign the sorted, &-joined "k=v" of their request body (excluding the
#  signature field) with their shared secret. Mirrors epusdt's scheme but with
#  HMAC-SHA256 instead of plain MD5 (auth, not just integrity).
# ---------------------------------------------------------------------------
def sign_params(secret: str, params: dict) -> str:
    payload = "&".join(
        f"{k}={params[k]}"
        for k in sorted(params)
        if k not in ("signature", "sign") and params[k] not in (None, "")
    )
    return hmac_sha256(secret, payload)


def verify_params(secret: str, params: dict) -> bool:
    given = str(params.get("signature") or params.get("sign") or "")
    return constant_time_equals(given, sign_params(secret, params))


# ---------------------------------------------------------------------------
#  Outbound webhook signing — what a merchant checks to trust our callback.
#  We send an X-OPG-Signature header AND a `signature` field, both HMAC-SHA256
#  over the canonical JSON payload. Merchants recompute and compare.
# ---------------------------------------------------------------------------
def sign_webhook(secret: str, payload: dict) -> str:
    return sign_params(secret, payload)


def new_timestamp_ms() -> int:
    return int(time.time() * 1000)
