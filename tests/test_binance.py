"""Binance verification: field matchers, the end-to-end verify (mocked network),
the merchant-webhook signature, and the anti-replay reference lock."""
import hashlib
import hmac
import json

import pytest

from optimus_gateway import binance
from optimus_gateway.binance import (
    BinanceAccount, BinanceVerifier, reference_candidates, status_ok,
    amount_matches, autocorrect_amount, destination_matches, verify_pay_webhook,
)

# A realistic Binance Pay P2P history row: the numeric order id the buyer submits
# lives in `orderId`; the internal id is `transactionId`; the receiver is us.
PAY_ROW = {
    "transactionId": "P_A237QRA5FFS7111B",
    "orderId": "443746280424488960",
    "orderType": "C2C",
    "status": "SUCCESS",
    "amount": "4.00",
    "currency": "USDT",
    "transactionTime": 1_700_000_000_000,
    "receiverInfo": {"binanceId": 1255707103, "name": "OPTIMUS"},
}
OUR_PAY_ID = "1255707103"
SUBMITTED_REF = "443746280424488960"


# ---- pure field matchers ---------------------------------------------------
def test_reference_candidates_covers_every_id_field():
    cands = reference_candidates(PAY_ROW)
    assert "443746280424488960" in cands          # orderId (what the buyer submits)
    assert "A237QRA5FFS7111B" in cands             # transactionId, normalized


def test_status_ok():
    assert status_ok({"status": "SUCCESS"})
    assert status_ok({"transactionStatus": "PAID"})
    assert status_ok({})                            # absent -> already settled
    assert not status_ok({"status": "PENDING"})
    assert not status_ok({"status": "FAILED"})


def test_amount_matches_exact_floor_and_tolerance():
    assert amount_matches(PAY_ROW, 4.00, "USDT")
    assert amount_matches({"amount": "4.007", "currency": "USDT"}, 4.00, "USDT")   # 2dp floor
    assert amount_matches({"amount": "3.99", "currency": "USDT"}, 4.00, "USDT")    # within delta
    assert not amount_matches({"amount": "3.50", "currency": "USDT"}, 4.00, "USDT")
    assert not amount_matches({"amount": "4.00", "currency": "EUR"}, 4.00, "USDT")  # currency mismatch
    assert amount_matches(PAY_ROW, None)            # no expectation -> pass


def test_autocorrect_returns_actual_amount():
    amount, currency, delta = autocorrect_amount({"amount": "3.99", "currency": "USDT"}, 4.00, "USDT")
    assert amount == 3.99 and currency == "USDT" and delta <= 0.02
    assert autocorrect_amount({"amount": "3.50", "currency": "USDT"}, 4.00, "USDT")[0] is None


def test_destination_match_blocks_a_payment_to_someone_else():
    assert destination_matches(PAY_ROW, OUR_PAY_ID)
    assert not destination_matches(PAY_ROW, "999999999")     # paid a different merchant
    assert destination_matches(PAY_ROW, "")                  # no receiver configured -> skip


# ---- end-to-end verify (network mocked) ------------------------------------
def _verifier_returning(rows):
    acc = BinanceAccount(api_key="k", api_secret="s", pay_id=OUR_PAY_ID)
    v = BinanceVerifier(acc)
    v._signed_get = lambda path, params, timeout=20: (200, json.dumps({"data": rows}))
    return v


def test_verify_pay_reference_happy_path():
    r = _verifier_returning([PAY_ROW]).verify_pay_reference(SUBMITTED_REF, 4.00, "USDT")
    assert r["ok"] and r["amount"] == 4.00 and r["reference"] == SUBMITTED_REF


def test_verify_rejects_wrong_amount():
    r = _verifier_returning([PAY_ROW]).verify_pay_reference(SUBMITTED_REF, 9.99, "USDT")
    assert not r["ok"] and r["reason"] == "not_found"


def test_verify_rejects_payment_to_another_receiver():
    row = dict(PAY_ROW, receiverInfo={"binanceId": 777})
    r = _verifier_returning([row]).verify_pay_reference(SUBMITTED_REF, 4.00, "USDT")
    assert not r["ok"]                              # right ref+amount but wrong receiver


def test_verify_rejects_non_numeric_reference():
    r = _verifier_returning([PAY_ROW]).verify_pay_reference("P_A237QRA5FFS7111B", 4.00)
    assert not r["ok"] and r["reason"] == "not_a_pay_order_id"


def test_verify_not_found():
    r = _verifier_returning([PAY_ROW]).verify_pay_reference("100000000000000001", 4.00)
    assert not r["ok"] and r["reason"] == "not_found"


def test_min_age_guard():
    import time
    recent = dict(PAY_ROW, transactionTime=int(time.time() * 1000))
    r = _verifier_returning([recent]).verify_pay_reference(SUBMITTED_REF, 4.00, min_age_minutes=10)
    assert not r["ok"] and r["reason"] == "too_recent"


# ---- merchant webhook signature (HMAC-SHA512, fail-closed) ------------------
def test_webhook_signature_roundtrip():
    secret, ts, nonce, body = "sec", "1700", "abc", b'{"bizStatus":"PAY_SUCCESS"}'
    payload = f"{ts}\n{nonce}\n{body.decode()}\n"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha512).hexdigest().upper()
    assert verify_pay_webhook(body, ts, nonce, sig, secret)
    assert not verify_pay_webhook(body, ts, nonce, "deadbeef", secret)   # forged
    assert not verify_pay_webhook(body, ts, nonce, sig, "")              # no secret -> fail closed


# ---- anti-replay reference lock -------------------------------------------
def test_claim_reference_burns_once(tmp_path, monkeypatch):
    # db.connect() reads config.DB_PATH on every call, so overriding it is enough.
    from optimus_gateway import db
    from optimus_gateway.config import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    assert db.claim_reference(SUBMITTED_REF, "binance_pay") is True     # first time
    assert db.claim_reference(SUBMITTED_REF, "binance_pay") is False    # replay blocked
    assert db.reference_used(SUBMITTED_REF) is True
    assert db.release_reference(SUBMITTED_REF) is True
    assert db.claim_reference(SUBMITTED_REF, "binance_pay") is True     # freed -> claimable again
