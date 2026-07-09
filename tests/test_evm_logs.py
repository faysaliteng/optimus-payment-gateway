"""
Tests for optimus_gateway.evm.get_logs_transfers — the EVM log parser that feeds the
watcher. Hermetic: evm.rpc is monkeypatched to return canned eth_getLogs results, so
no network is touched. We prove:

  * only logs for the RIGHT contract + Transfer topic + one of our TO addresses are
    trusted (a malicious/rotated RPC cannot forge a credit by returning junk);
  * each transfer carries its log_index, so a single tx emitting several Transfer
    events can be credited independently instead of colliding on the txid.
"""
from __future__ import annotations

from optimus_gateway import evm
from optimus_gateway.chains import EVM_TRANSFER_TOPIC, to_topic_address

CONTRACT = "0x55d398326f99059ff775485246999027b3197955"   # BSC USDT
USDC = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"        # BSC USDC
ADDR = "0xabc0000000000000000000000000000000000001"
OTHER = "0xdef0000000000000000000000000000000000002"


def _log(*, contract=CONTRACT, to=ADDR, amount=10 ** 18, txid="0x" + "aa" * 32,
         log_index=0, block=100, topic0=EVM_TRANSFER_TOPIC, frm=OTHER):
    return {
        "address": contract,
        "topics": [topic0, "0x" + frm.replace("0x", "").rjust(64, "0"), to_topic_address(to)],
        "data": hex(amount),
        "transactionHash": txid,
        "logIndex": hex(log_index),
        "blockNumber": hex(block),
    }


def _patch(monkeypatch, logs):
    monkeypatch.setattr(evm, "rpc", lambda eps, method, params: logs)


def test_valid_transfer_is_parsed_with_log_index(monkeypatch):
    _patch(monkeypatch, [_log(amount=25 * 10 ** 18, log_index=7)])
    transfers, ok = evm.get_logs_transfers(["http://x"], CONTRACT, [ADDR], 1, 100)
    assert ok is True
    assert len(transfers) == 1
    t = transfers[0]
    assert t["to"] == ADDR.lower()
    assert t["raw"] == 25 * 10 ** 18
    assert t["log_index"] == 7           # extracted from the hex logIndex


def test_two_transfers_same_tx_keep_distinct_log_indexes(monkeypatch):
    """A batch/multisend paying our address twice in one tx must surface BOTH events
    with different log_index values (so each credits independently downstream)."""
    txid = "0x" + "bb" * 32
    _patch(monkeypatch, [
        _log(txid=txid, log_index=3, amount=10 ** 18),
        _log(txid=txid, log_index=9, amount=2 * 10 ** 18),
    ])
    transfers, ok = evm.get_logs_transfers(["http://x"], CONTRACT, [ADDR], 1, 100)
    assert ok is True
    assert {t["log_index"] for t in transfers} == {3, 9}
    assert all(t["txid"] == txid for t in transfers)


def test_forged_logs_are_rejected(monkeypatch):
    """A lying RPC returns: wrong contract, wrong topic0, and a transfer to an address
    we didn't ask about. None may be trusted."""
    _patch(monkeypatch, [
        _log(contract="0x" + "11" * 20),                 # wrong contract
        _log(topic0="0x" + "00" * 32),                   # not a Transfer event
        _log(to="0x9999999999999999999999999999999999999999"),  # not our address
    ])
    transfers, ok = evm.get_logs_transfers(["http://x"], CONTRACT, [ADDR], 1, 100)
    assert ok is True
    assert transfers == []


def test_zero_amount_transfer_ignored(monkeypatch):
    _patch(monkeypatch, [_log(amount=0)])
    transfers, ok = evm.get_logs_transfers(["http://x"], CONTRACT, [ADDR], 1, 100)
    assert ok is True and transfers == []


def test_rpc_failure_reports_not_ok(monkeypatch):
    """rpc returning None (all endpoints failed) -> ok=False so the caller does NOT
    advance its block cursor (nothing is ever skipped)."""
    monkeypatch.setattr(evm, "rpc", lambda eps, method, params: None)
    transfers, ok = evm.get_logs_transfers(["http://x"], CONTRACT, [ADDR], 1, 100)
    assert ok is False and transfers == []


def test_contract_list_folds_multiple_tokens(monkeypatch):
    """A LIST of contracts scans several stablecoins in ONE call: a log for any contract
    in the set is accepted and tagged with its `contract`, while a log for a contract
    outside the set is rejected (a rotated/lying RPC can't slip in an unwatched token)."""
    good_usdt = _log(contract=CONTRACT, amount=5 * 10 ** 18, log_index=1)
    good_usdc = _log(contract=USDC, amount=7 * 10 ** 18, log_index=2)
    alien = _log(contract="0x" + "11" * 20, amount=9 * 10 ** 18, log_index=3)  # not watched
    _patch(monkeypatch, [good_usdt, good_usdc, alien])
    transfers, ok = evm.get_logs_transfers(["http://x"], [CONTRACT, USDC], [ADDR], 1, 100)
    assert ok is True
    assert {t["contract"]: t["raw"] for t in transfers} == {
        CONTRACT.lower(): 5 * 10 ** 18, USDC.lower(): 7 * 10 ** 18}


def test_address_param_is_array_only_for_multiple_contracts(monkeypatch):
    """eth_getLogs `address` must be the ARRAY of contracts when >1 is passed (one
    OR-filtered call), and the bare string when only one — so a single-token chain keeps
    the exact request shape it always had."""
    seen = {}

    def fake_rpc(eps, method, params):
        seen["address"] = params[0]["address"]
        return []
    monkeypatch.setattr(evm, "rpc", fake_rpc)
    evm.get_logs_transfers(["http://x"], [CONTRACT, USDC], [ADDR], 1, 100)
    assert seen["address"] == [CONTRACT, USDC]
    evm.get_logs_transfers(["http://x"], CONTRACT, [ADDR], 1, 100)
    assert seen["address"] == CONTRACT
