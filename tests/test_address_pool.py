"""
Tests for the accumulating address pool (optimus_gateway.db). OFF by default (existing
behavior unchanged); ON, it reuses per-order addresses so small payments accumulate and
sweep once. The money-safety property proven here: a reused address ALWAYS credits its
current OPEN order — never a previous buyer. Hermetic: throwaway SQLite, no network.
"""
from __future__ import annotations

import pytest

from optimus_gateway import db
from optimus_gateway.config import config

DERIVE = lambda i: "0x" + format(int(i), "040x")   # deterministic per-index EVM address


@pytest.fixture
def pool_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "pool_test.db"))
    db.init_db()
    yield


def _age_and_close(order_id, off="-3 days"):
    """Force an order into a closed + past-cooldown state so its address is reissuable."""
    conn = db.connect()
    conn.execute(
        "UPDATE orders SET status='paid', created_at=datetime('now',?), "
        "last_activity_at=datetime('now',?), reservation_expires_at=datetime('now',?) WHERE id=?",
        (off, off, off, order_id))
    conn.commit()


def _order_state(order_id):
    conn = db.connect()
    r = conn.execute("SELECT received_cents, status FROM orders WHERE id=?", (order_id,)).fetchone()
    return int(r["received_cents"] or 0), r["status"]


def test_pool_off_mints_fresh_indexes(pool_db):
    db.set_setting("pool_enabled", "false")
    a = db.create_addressed_order("usdt_bep20", 5.0, DERIVE, merchant_order_id="A")
    _age_and_close(a["id"])                       # closed + old, but pool is OFF
    b = db.create_addressed_order("usdt_bep20", 5.0, DERIVE, merchant_order_id="B")
    assert b["address_index"] != a["address_index"]      # never reused when OFF


def test_pool_on_reuses_a_cooled_down_closed_address(pool_db):
    db.set_setting("pool_enabled", "true")
    db.set_setting("pool_size", "2")
    db.set_setting("pool_reuse_cooldown_minutes", "1")
    a = db.create_addressed_order("usdt_bep20", 5.0, DERIVE, merchant_order_id="A")
    _age_and_close(a["id"], "-3 days")                 # closed 5 min ago (> 1 min cooldown)
    b = db.create_addressed_order("usdt_bep20", 1.0, DERIVE, merchant_order_id="B")
    assert b["address_index"] == a["address_index"]       # REUSED the freed address
    assert b["pay_address"] == a["pay_address"]


def test_open_order_locks_the_address_from_reuse(pool_db):
    db.set_setting("pool_enabled", "true")
    db.set_setting("pool_size", "1")                      # only 1 pooled index
    db.set_setting("pool_reuse_cooldown_minutes", "1")
    a = db.create_addressed_order("usdt_bep20", 5.0, DERIVE, merchant_order_id="A")  # OPEN, unpaid
    # a's address is pending -> NOT reissuable even though pool_size=1; B must mint a NEW index
    b = db.create_addressed_order("usdt_bep20", 1.0, DERIVE, merchant_order_id="B")
    assert b["address_index"] != a["address_index"]       # pending address never handed out


def test_reused_address_credits_the_open_buyer_not_a_prior_one(pool_db):
    """THE money-safety case: two buyers ever share an address; a payment must credit the
    CURRENT open order, never the previous occupant."""
    db.set_setting("pool_enabled", "true")
    db.set_setting("pool_size", "1")
    db.set_setting("pool_reuse_cooldown_minutes", "1")
    a = db.create_addressed_order("usdt_bep20", 5.0, DERIVE, merchant_order_id="prior")
    _age_and_close(a["id"], "-3 days")
    b = db.create_addressed_order("usdt_bep20", 1.0, DERIVE, merchant_order_id="current")
    addr = b["pay_address"]
    assert b["address_index"] == a["address_index"] and addr == a["pay_address"]   # reused
    res = db.credit_by_address("usdt_bep20", addr, 100, "0x" + "cd" * 32)
    assert res["status"] == "paid"
    assert _order_state(b["id"]) == (100, "paid")         # buyer B (open) credited
    assert _order_state(a["id"])[0] == 0                  # prior buyer A untouched
    # idempotent: replaying the same txid credits nothing more
    db.credit_by_address("usdt_bep20", addr, 100, "0x" + "cd" * 32)
    assert _order_state(b["id"]) == (100, "paid")


def test_sweep_dedup_groups_by_address_and_marks_all_rows(pool_db):
    db.set_setting("pool_enabled", "true")
    db.set_setting("pool_size", "1")
    db.set_setting("pool_reuse_cooldown_minutes", "1")
    a = db.create_addressed_order("usdt_bep20", 4.0, DERIVE, merchant_order_id="acc-a")
    addr = a["pay_address"]
    db.credit_by_address("usdt_bep20", addr, 400, "0x" + "a1" * 32)   # $4 to A
    _age_and_close(a["id"], "-3 days")                            # (credit set it paid; age it)
    b = db.create_addressed_order("usdt_bep20", 6.5, DERIVE, merchant_order_id="acc-b")
    assert b["pay_address"] == addr                                   # reused, coins accumulate
    db.credit_by_address("usdt_bep20", addr, 650, "0x" + "b2" * 32)   # +$6.50 to B, same address
    swept = [r for r in db.sweepable_order_addresses("usdt_bep20") if r["pay_address"].lower() == addr.lower()]
    assert len(swept) == 1                                            # ONE row per address (de-duped)
    db.mark_address_swept(addr, "0x" + "ff" * 32)
    assert not any(r["pay_address"].lower() == addr.lower() for r in db.sweepable_order_addresses("usdt_bep20"))
