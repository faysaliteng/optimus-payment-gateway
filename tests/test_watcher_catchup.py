"""
Tests for watcher.scan_evm's catch-up bounding and the single-call-per-chunk fold — the
two properties that keep a rate-limited public node (Polygon) from stalling the cursor:

  * scan_to is capped by the PER-CHAIN `max_catchup` (Polygon 400), not the global
    OPG_MAX_CATCHUP_BLOCKS default (1500);
  * all of a chain's watched stablecoins (USDT + USDC + …) are scanned in ONE
    get_logs_transfers call per block-chunk, so the per-cycle getLogs count is
    (blocks / max_span) — independent of how many tokens are accepted. Scanning per token
    would multiply the call count and trip Polygon's rate limit mid-catch-up, which (with
    the all-or-nothing cursor advance) is the exact swept-without-credit stall this guards.

Also asserts the import-time invariant that a chain's max_catchup must exceed RESCAN_OVERLAP
(a smaller value would drive the cursor backward every tick).

Hermetic: throwaway SQLite + monkeypatched evm, no network.
"""
from __future__ import annotations

import math

import pytest

from optimus_gateway import db, evm, watcher
from optimus_gateway.chains import CHAINS
from optimus_gateway.config import config
from optimus_gateway.watcher import _watched_tokens

ADDR = "0xaa01000000000000000000000000000000000001"


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "catchup_test.db"))
    db.init_db()
    yield


def test_polygon_catchup_capped_by_per_chain_max_catchup(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "xpub", lambda: "xpub-dummy")        # per-order mode
    monkeypatch.setattr(config, "accept_usdc", lambda: True)          # watch USDT + USDC(.e)
    monkeypatch.setattr(evm, "block_number", lambda eps: 1_000_000)   # tip far ahead of cursor

    db.create_order("usdt_polygon", 50.00, pay_address=ADDR, address_index=1)
    db.set_setting("polygon_watch_last_block", "1000")

    calls = []

    def fake_get_logs(eps, contract, addrs, frm, to):
        calls.append({"contract": contract, "from": frm, "to": to})
        return [], True
    monkeypatch.setattr(evm, "get_logs_transfers", fake_get_logs)

    res = watcher.scan_evm("usdt_polygon")
    assert res["ok"] is True

    overlap = config.RESCAN_OVERLAP
    max_catchup = CHAINS["usdt_polygon"]["max_catchup"]
    span = CHAINS["usdt_polygon"]["max_span"]
    from_block = 1000 + 1 - overlap
    expected_scan_to = from_block + max_catchup - 1

    # (1) cursor bounded by the PER-CHAIN max_catchup (400), NOT the global 1500 or the tip
    assert max_catchup < config.MAX_CATCHUP_BLOCKS          # test is meaningful (400 < 1500)
    assert int(db.get_setting("polygon_watch_last_block")) == expected_scan_to

    # (2) exactly ONE getLogs per block-chunk, regardless of the 2-3 watched tokens
    assert len(calls) == math.ceil(max_catchup / span)
    assert calls[0]["from"] == from_block

    # (3) each call folds ALL watched stablecoins into one contract-array argument
    watched = list(_watched_tokens("usdt_polygon").values())
    assert len(watched) >= 2                                # the fold is actually exercised
    assert all(c["contract"] == watched for c in calls)


def test_all_evm_chains_have_max_catchup_above_overlap():
    """The import-time guard in chains.py must hold for every shipped EVM chain: a
    max_catchup <= RESCAN_OVERLAP would write the block cursor backward and stall."""
    overlap = config.RESCAN_OVERLAP
    default = config.MAX_CATCHUP_BLOCKS
    for method, cfg in CHAINS.items():
        if cfg.get("scanner") != "evm":
            continue
        effective = int(cfg.get("max_catchup", default))
        assert effective > overlap, f"{method}: max_catchup {effective} <= overlap {overlap}"
