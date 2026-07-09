"""
Tests for the SSRF guard on merchant callback URLs and the safe-by-default merchant
API authorization decision (security.is_safe_webhook_url / authorize_merchant).

We use IP-literal hosts so no real DNS/network is needed (getaddrinfo resolves a
numeric host to itself).
"""
from __future__ import annotations

from optimus_gateway.security import (
    authorize_merchant, is_safe_webhook_url, is_loopback_host, sign_params,
)

PUBLIC = "https://93.184.216.34/callback"     # numeric public IP (no DNS lookup)


# --- SSRF guard -------------------------------------------------------------
def test_public_url_is_allowed():
    assert is_safe_webhook_url(PUBLIC) is True


def test_private_and_loopback_urls_are_blocked():
    for bad in [
        "http://127.0.0.1/hook",            # loopback
        "http://10.0.0.5/hook",             # private
        "http://192.168.1.10/hook",         # private
        "http://169.254.169.254/latest",    # cloud metadata (link-local)
        "http://[::1]/hook",                # IPv6 loopback
    ]:
        assert is_safe_webhook_url(bad) is False, bad


def test_non_http_scheme_blocked():
    assert is_safe_webhook_url("ftp://93.184.216.34/x") is False
    assert is_safe_webhook_url("file:///etc/passwd") is False
    assert is_safe_webhook_url("") is False


def test_allow_private_opt_in_bypasses_guard():
    assert is_safe_webhook_url("http://127.0.0.1:9000/hook", allow_private=True) is True


def test_is_loopback_host():
    assert is_loopback_host("127.0.0.1") and is_loopback_host("::1") and is_loopback_host("localhost")
    assert not is_loopback_host("8.8.8.8")


# --- merchant authorization -------------------------------------------------
KEY = "pk_live_abc"
SECRET = "sk_live_xyz"


def _signed_body(**fields):
    body = {"api_key": KEY, **fields}
    body["signature"] = sign_params(SECRET, body)
    return body


def test_valid_key_and_signature_authorized():
    body = _signed_body(method="usdt_bep20", amount="25.00", order_id="INV-1")
    ok, err = authorize_merchant(body, "8.8.8.8", api_key=KEY, api_secret=SECRET)
    assert ok is True and err == ""


def test_wrong_key_rejected():
    body = _signed_body(method="usdt_bep20", amount="25.00")
    body["api_key"] = "pk_wrong"
    ok, err = authorize_merchant(body, "8.8.8.8", api_key=KEY, api_secret=SECRET)
    assert ok is False and "api_key" in err


def test_tampered_amount_fails_signature():
    body = _signed_body(method="usdt_bep20", amount="25.00")
    body["amount"] = "999.00"      # tamper after signing
    ok, err = authorize_merchant(body, "8.8.8.8", api_key=KEY, api_secret=SECRET)
    assert ok is False and "signature" in err


def test_no_key_allows_local_but_blocks_remote():
    body = {"method": "usdt_bep20", "amount": "25.00"}
    ok_local, _ = authorize_merchant(body, "127.0.0.1", api_key="", api_secret="")
    ok_remote, err = authorize_merchant(body, "203.0.113.9", api_key="", api_secret="")
    assert ok_local is True
    assert ok_remote is False and "unauthenticated" in err


def test_no_key_remote_allowed_when_explicitly_opted_in():
    body = {"method": "usdt_bep20", "amount": "25.00"}
    ok, err = authorize_merchant(body, "203.0.113.9", api_key="", api_secret="",
                                 allow_unauthenticated=True)
    assert ok is True and err == ""
