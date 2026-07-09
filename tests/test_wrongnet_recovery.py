"""
Tests for the unified crediting model (the wrong-network ledger was removed):

  * a single tx with several Transfer events credits EACH (txid, logIndex);
  * WRONG-NETWORK deposits are credited by the watcher because credit_by_address matches
    by ADDRESS alone and is method-agnostic (the watcher now scans every per-order
    address on every chain via all_active_order_addresses);
  * the anti-replay key is backward-compatible across the #suffix upgrade (a legacy bare
    key still blocks a suffixed re-claim of the same txid);
  * all_active_order_addresses returns per-order addresses across ALL methods.

Hermetic: throwaway SQLite via config.DB_PATH, no network.
"""
from __future__ import annotations

import pytest

from optimus_gateway import db
from optimus_gateway.config import config
from optimus_gateway.security import normalize_reference

TXID = "0x" + "cc" * 32


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "wn_test.db"))
    db.init_db()
    yield


def test_same_txid_different_log_index_credits_each(fresh_db):
    """Batch/multisend: one txid, two Transfer events -> both credit; replaying the same
    (txid, logIndex) credits nothing."""
    addr = "0xba7c000000000000000000000000000000000011"
    db.create_order("usdt_bep20", 40.00, pay_address=addr, address_index=11)
    a = db.credit_by_address("usdt_bep20", addr, 2000, TXID, "0")
    assert a["status"] == "partial"
    b = db.credit_by_address("usdt_bep20", addr, 2000, TXID, "1")
    assert b["status"] == "paid" and b["received_cents"] == 4000
    replay = db.credit_by_address("usdt_bep20", addr, 2000, TXID, "0")
    assert replay["status"] == "already_used"


def test_wrong_network_deposit_credits_the_order(fresh_db):
    """An order quoted on Ethereum, paid on BSC (wrong chain), is credited: the watcher
    scans the address on every chain and credit_by_address matches by address alone."""
    addr = "0xc0ffee0000000000000000000000000000000012"
    order = db.create_order("usdt_erc20", 25.00, pay_address=addr, address_index=12)
    # buyer paid on usdt_bep20 (the WRONG chain for this erc20 order)
    res = db.credit_by_address("usdt_bep20", addr, 2500, TXID, "0")
    assert res["status"] == "paid" and res["trade_id"] == order["trade_id"]
    # idempotent: the same wrong-chain transfer seen again credits nothing
    again = db.credit_by_address("usdt_bep20", addr, 2500, TXID, "0")
    assert again["status"] == "already_used"


def test_legacy_bare_reference_blocks_suffixed_reclaim(fresh_db):
    """Upgrade safety: a txid burned pre-upgrade (bare normalized key, no suffix) must not
    be re-credited when the post-upgrade watcher re-scans it with a #logIndex suffix."""
    addr = "0xc0dec0000000000000000000000000000000013"
    db.create_order("usdt_bep20", 25.00, pay_address=addr, address_index=13)
    # simulate a pre-upgrade credit: a bare normalized_reference row exists
    conn = db.connect()
    conn.execute(
        "INSERT INTO payment_reference_registry(normalized_reference, original_reference,"
        " reference_type, order_id) VALUES(?,?,?,?)",
        (normalize_reference(TXID), TXID, "usdt_bep20", 1))
    conn.commit()
    conn.close()
    # post-upgrade re-scan of the same txid (now with a logIndex suffix) must be blocked
    res = db.credit_by_address("usdt_bep20", addr, 2500, TXID, "0")
    assert res["status"] == "already_used"
    assert int(db.get_order(trade_id=db.list_orders()[0]["trade_id"])["received_cents"]) == 0


def test_all_active_order_addresses_spans_methods(fresh_db):
    """Every pending per-order address is returned regardless of method (so each chain's
    watcher can catch a wrong-network payment to any of them)."""
    a1 = db.create_order("usdt_bep20", 10.0, pay_address="0xa1" + "0" * 38, address_index=1)
    a2 = db.create_order("usdt_erc20", 10.0, pay_address="0xa2" + "0" * 38, address_index=2)
    # amount-match order (no address_index) must NOT appear
    db.create_order("usdt_polygon", 10.0, pay_address="0xshared" + "0" * 33, amount_match=True)
    addrs = {r["pay_address"] for r in db.all_active_order_addresses()}
    assert a1["pay_address"] in addrs and a2["pay_address"] in addrs
    assert ("0xshared" + "0" * 33) not in addrs
