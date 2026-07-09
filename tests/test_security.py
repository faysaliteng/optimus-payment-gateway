"""
Tests for optimus_gateway.security — the primitives every money-moving path leans on:

  * sign_params / verify_params  — the HMAC-SHA256 request/webhook auth scheme.
  * normalize_reference          — the canonical key that burns a txid into the
                                   anti-replay registry (so a re-scan can't double-credit).
  * constant_time_equals         — timing-safe comparison.

These are pure functions with no I/O, so the whole module is hermetic (no network,
no DB, no filesystem).
"""
from __future__ import annotations

from optimus_gateway.security import (
    constant_time_equals,
    normalize_reference,
    sign_params,
    verify_params,
)

SECRET = "a-long-random-merchant-secret"


# --- sign_params / verify_params -------------------------------------------
def test_sign_verify_roundtrip():
    """A params dict signed with the secret verifies against the same secret."""
    params = {
        "api_key": "pk_live_123",
        "method": "usdt_bep20",
        "amount": "25.00",
        "order_id": "INV-1001",
    }
    params["signature"] = sign_params(SECRET, params)
    assert verify_params(SECRET, params) is True


def test_sign_is_stable_and_order_independent():
    """The signature is computed over SORTED keys, so dict insertion order and the
    presence of the signature field itself never change the result."""
    a = {"b": "2", "a": "1", "c": "3"}
    b = {"c": "3", "a": "1", "b": "2"}
    assert sign_params(SECRET, a) == sign_params(SECRET, b)

    # Adding the signature field back in must not change what gets signed (it is
    # excluded), which is exactly what makes verify_params work.
    signed = dict(a, signature="ignored")
    assert sign_params(SECRET, signed) == sign_params(SECRET, a)


def test_sign_ignores_empty_and_none_values():
    """Empty / None values are dropped before signing — a merchant that omits an
    optional field and one that sends it blank produce the same signature. This is
    what keeps webhook payloads (which may carry a null merchant_order_id) verifiable."""
    with_blank = {"amount": "10.00", "method": "usdt_bep20", "memo": "", "note": None}
    without = {"amount": "10.00", "method": "usdt_bep20"}
    assert sign_params(SECRET, with_blank) == sign_params(SECRET, without)


def test_verify_accepts_sign_alias():
    """verify_params reads either `signature` or the legacy `sign` field name."""
    params = {"amount": "5.00", "method": "usdt_ton"}
    params["sign"] = sign_params(SECRET, params)
    assert verify_params(SECRET, params) is True


def test_verify_fails_on_tampering():
    """Any change to a signed field, or the wrong secret, must fail verification."""
    params = {"amount": "25.00", "method": "usdt_bep20", "order_id": "INV-1"}
    params["signature"] = sign_params(SECRET, params)

    tampered = dict(params, amount="2500.00")   # attacker bumps the amount
    assert verify_params(SECRET, tampered) is False

    assert verify_params("the-wrong-secret", params) is False

    missing = {k: v for k, v in params.items() if k != "signature"}
    assert verify_params(SECRET, missing) is False   # no signature at all


# --- normalize_reference ----------------------------------------------------
def test_normalize_reference_collapses_formatting():
    """The SAME on-chain txid, copied with any surrounding noise, must collapse to
    ONE registry key — that single-key guarantee is what makes crediting idempotent.

    normalize_reference upper-cases, strips whitespace / backticks / quotes, and
    picks the longest alphanumeric token that contains a digit (so a pasted label
    like 'Tx: 0x…' reduces to the hash). The 0x-prefixed hash is preserved verbatim,
    but every formatting of it maps to the same canonical value.
    """
    canonical = normalize_reference("0xDEADbeef1234")
    assert canonical  # non-empty

    variants = [
        "  0xDEADbeef1234  ",          # surrounding whitespace
        "0xdeadbeef1234",              # different case
        "`0xDEADBEEF1234`",            # markdown backticks
        "'0xDeAdBeEf1234'",            # quotes + mixed case
        "Tx 0xDEADbeef1234 confirmed", # label noise around the hash
        "0xDEADbeef1234\n",            # trailing newline
    ]
    for v in variants:
        assert normalize_reference(v) == canonical, v


def test_normalize_reference_case_and_whitespace():
    assert normalize_reference("abc123") == normalize_reference("ABC123")
    assert normalize_reference("  abc123  ") == "ABC123"


def test_normalize_reference_empty():
    assert normalize_reference("") == ""
    assert normalize_reference(None) == ""


def test_normalize_reference_is_deterministic():
    ref = "0x9f8e7d6c5b4a39281706"
    assert normalize_reference(ref) == normalize_reference(ref)


# --- constant_time_equals ---------------------------------------------------
def test_constant_time_equals():
    assert constant_time_equals("abc", "abc") is True
    assert constant_time_equals("abc", "abd") is False
    assert constant_time_equals("abc", "abcd") is False
    # None / "" are coerced to the empty string, so both-empty compares equal and a
    # value vs. empty does not.
    assert constant_time_equals("", None) is True
    assert constant_time_equals(None, None) is True
    assert constant_time_equals("x", None) is False
