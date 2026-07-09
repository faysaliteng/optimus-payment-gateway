"""
Tests for optimus_gateway.db — the exact-cents, idempotent, credit-not-consume ledger.

The money invariants proven here (see db.py header):
  * amounts are integer CENTS end to end;
  * a txid is BURNED before it credits, so the same txid seen twice credits ONCE
    (a re-scan / retry / replay can never double-credit);
  * an order flips to PAID only once the running total COVERS the expected amount;
  * a later, different txid does not "un-pay" or vanish — it is recorded as a top-up
    with overpaid_cents > 0;
  * amount-match mode credits the order that reserved that exact cents value.

Hermetic: each test runs against a throwaway SQLite file (temp OPG_DB_PATH), no
network, no shared state. We point config.DB_PATH at the temp file BEFORE init_db();
db.connect() reads config.DB_PATH at call time, so no module reload is needed.
"""
from __future__ import annotations

import pytest

from optimus_gateway import db
from optimus_gateway.config import config

METHOD = "usdt_bep20"
# Two distinct, realistic-looking txids (they normalise to different registry keys).
TXID_1 = "0x" + "11" * 32
TXID_2 = "0x" + "22" * 32
TXID_3 = "0x" + "33" * 32


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    """A fresh, empty gateway DB per test."""
    db_file = tmp_path / "ledger_test.db"
    # db.connect() reads config.DB_PATH on every call, so overriding the attribute is
    # enough to redirect ALL storage at the temp file. monkeypatch restores it after.
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    db.init_db()
    yield str(db_file)


# --- per-order-address crediting -------------------------------------------
def test_credit_by_address_is_idempotent_and_flips_to_paid(ledger_db):
    """Create an order, credit the full amount, and prove: (1) it flips to paid when
    covered, and (2) replaying the SAME txid credits nothing more."""
    addr = "0xAbC0000000000000000000000000000000000001"
    order = db.create_order(
        METHOD, 25.00, merchant_order_id="INV-idem-1",
        pay_address=addr, address_index=1,
    )
    assert order["status"] == db.STATUS_PENDING
    assert order["expected_cents"] == 2500          # 25.00 USD -> 2500 cents
    assert int(order["received_cents"] or 0) == 0

    # First sighting of TXID_1 for the full amount -> covered -> paid.
    first = db.credit_by_address("usdt_bep20", addr, 2500, TXID_1)
    assert first["status"] == "paid"
    assert first["received_cents"] == 2500
    assert first["overpaid_cents"] == 0

    # Same txid seen again (a re-scan): burned reference -> credited ONCE.
    replay = db.credit_by_address("usdt_bep20", addr, 2500, TXID_1)
    assert replay["status"] == "already_used"

    # The ledger did not move: still 2500 received, still exactly one tx recorded.
    o = db.get_order(trade_id=order["trade_id"])
    assert o["status"] == db.STATUS_PAID
    assert int(o["received_cents"]) == 2500
    assert o["tx_hashes"].count(TXID_1) == 1


def test_second_different_txid_records_overpayment(ledger_db):
    """A distinct later txid to a paid order is not lost — it accumulates and is
    reported as a top-up with overpaid_cents > 0 (credit-not-consume)."""
    addr = "0xAbC0000000000000000000000000000000000002"
    order = db.create_order(METHOD, 25.00, pay_address=addr, address_index=2)

    db.credit_by_address("usdt_bep20", addr, 2500, TXID_1)      # -> paid (2500/2500)
    over = db.credit_by_address("usdt_bep20", addr, 2500, TXID_2)  # extra 2500

    assert over["status"] == "topup"          # already paid, so this is a top-up
    assert over["received_cents"] == 5000
    assert over["overpaid_cents"] == 2500     # 5000 received - 2500 expected

    o = db.get_order(trade_id=order["trade_id"])
    assert int(o["received_cents"]) == 5000
    assert TXID_1 in o["tx_hashes"] and TXID_2 in o["tx_hashes"]


def test_partial_then_covered(ledger_db):
    """Two under-payments (distinct txids) accumulate; the order flips to paid only
    when the running total finally covers the expected amount."""
    addr = "0xAbC0000000000000000000000000000000000003"
    order = db.create_order(METHOD, 30.00, pay_address=addr, address_index=3)

    part = db.credit_by_address("usdt_bep20", addr, 1000, TXID_1)   # 10.00 of 30.00
    assert part["status"] == "partial"
    assert part["received_cents"] == 1000
    assert db.get_order(trade_id=order["trade_id"])["status"] == db.STATUS_PENDING

    rest = db.credit_by_address("usdt_bep20", addr, 2000, TXID_2)   # +20.00 -> covered
    assert rest["status"] == "paid"
    assert rest["received_cents"] == 3000
    assert rest["overpaid_cents"] == 0
    assert db.get_order(trade_id=order["trade_id"])["status"] == db.STATUS_PAID


def test_credit_by_address_no_matching_order(ledger_db):
    """A transfer to an address no order owns is reported, not credited anywhere."""
    res = db.credit_by_address("usdt_bep20", "0xNoSuchAddress", 2500, TXID_1)
    assert res["status"] == "no_order"


# --- amount-match crediting -------------------------------------------------
def test_credit_by_amount_matches_reserved_cents(ledger_db):
    """Amount-match mode: the order reserves a unique cents value on a shared address;
    an on-chain transfer of exactly that many cents credits it and flips it to paid."""
    shared = "0xShared0000000000000000000000000000000000"
    order = db.create_order(
        METHOD, 30.00, pay_address=shared, amount_match=True,
    )
    reserved = int(order["expected_cents"])   # 3000, or nudged up if that was taken
    assert reserved >= 3000

    # Credit the EXACT reserved amount -> matched by (method, expected_cents, pending).
    res = db.credit_by_amount("usdt_bep20", reserved, TXID_3)
    assert res["status"] == "paid"
    assert res["trade_id"] == order["trade_id"]
    assert res["received_cents"] == reserved

    o = db.get_order(trade_id=order["trade_id"])
    assert o["status"] == db.STATUS_PAID

    # Replaying the same txid at the same amount does not double-credit; and now that
    # the order is paid, a fresh match no longer exists for that cents value.
    again = db.credit_by_amount("usdt_bep20", reserved, TXID_3)
    assert again["status"] in ("already_used", "no_order")


def test_reservations_get_unique_amounts(ledger_db):
    """Amount-match mode nudges each new reservation to a DISTINCT cents value so the
    on-chain amount uniquely identifies the order."""
    shared = "0xShared0000000000000000000000000000000000"
    a = db.create_order(METHOD, 30.00, pay_address=shared, amount_match=True)
    b = db.create_order(METHOD, 30.00, pay_address=shared, amount_match=True)
    assert a["expected_cents"] != b["expected_cents"]
