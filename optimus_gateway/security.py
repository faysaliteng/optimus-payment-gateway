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
import ipaddress
import re
import socket
import time
from urllib.parse import urlparse

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


# ---------------------------------------------------------------------------
#  SSRF guard for merchant-supplied callback URLs (notify_url / redirect_url).
#  A malicious or careless merchant_order_id caller could point notify_url at an
#  internal service (169.254.169.254 metadata, localhost admin, 10.x, …) and use
#  our server as a blind SSRF proxy. We refuse non-http(s) schemes and any host
#  that resolves to a private / loopback / link-local / reserved address unless
#  the operator explicitly opts in (OPG_ALLOW_PRIVATE_WEBHOOKS, for local dev).
# ---------------------------------------------------------------------------
def _ip_is_private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable -> treat as unsafe
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def is_safe_webhook_url(url: str, allow_private: bool = False) -> bool:
    """True if `url` is an http(s) URL whose host does not resolve to a private/
    internal address. With allow_private=True only the scheme/host sanity holds."""
    if not url:
        return False
    try:
        p = urlparse(str(url))
    except Exception:  # noqa: BLE001
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    if allow_private:
        return True
    host = p.hostname
    # Resolve every A/AAAA record; reject if ANY is internal (defeats a DNS name
    # that maps to a private IP). Unresolvable -> unsafe.
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001
        return False
    ips = {info[4][0] for info in infos}
    if not ips:
        return False
    return not any(_ip_is_private(ip) for ip in ips)


def is_loopback_host(host: str) -> bool:
    """True if the request came from the local machine (safe to trust when no
    merchant API key is configured — i.e. a dev / single-box deployment)."""
    if not host:
        return False
    if host in ("localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def authorize_merchant(body: dict, client_host: str, *, api_key: str, api_secret: str,
                       allow_unauthenticated: bool = False) -> tuple[bool, str]:
    """Decide whether a create-order request is allowed. Pure + unit-testable.

    * If a merchant API key is configured: require a matching api_key (constant-time)
      AND a valid HMAC signature over the body.
    * If NO key is configured: allow ONLY local-loopback callers (dev / single box),
      unless the operator explicitly opted into open mode (allow_unauthenticated).
      This makes the default deployment SAFE — a public, keyless gateway no longer
      lets anyone on the internet mint orders (spam + attacker-chosen notify_url).
    Returns (ok, error_message)."""
    if api_key:
        if not constant_time_equals(str(body.get("api_key") or ""), api_key):
            return False, "bad api_key"
        if not verify_params(api_secret, body):
            return False, "bad signature"
        return True, ""
    if allow_unauthenticated or is_loopback_host(client_host):
        return True, ""
    return False, ("merchant API is unauthenticated and this request is not local; "
                   "set OPG_MERCHANT_API_KEY (recommended) or OPG_ALLOW_UNAUTHENTICATED=true")
