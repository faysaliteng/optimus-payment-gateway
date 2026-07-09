"""
Low-level EVM helpers — keyless JSON-RPC, log scanning, balances, and transaction
signing/broadcast. No API keys required: public endpoints are rotated and every
node's answer is re-verified (a malicious RPC can't forge a credit).

A note on the User-Agent: several public Ethereum/Polygon nodes 403 the default
urllib UA, so we always send a browser-ish one.
"""
from __future__ import annotations

import json
import logging
import urllib.request

from .chains import EVM_TRANSFER_TOPIC, TRANSFER_SELECTOR, BALANCEOF_SELECTOR, to_topic_address

log = logging.getLogger("optimus_gateway.evm")

_UA = "Mozilla/5.0 (compatible; OptimusGateway/1.0)"
_RPC_TIMEOUT = 12


def rpc(endpoints: list[str], method: str, params: list):
    """Call `method` against the first endpoint that answers. Returns result or None."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for url in endpoints:
        try:
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json", "User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=_RPC_TIMEOUT) as r:
                j = json.load(r)
            if isinstance(j, dict) and j.get("result") is not None:
                return j["result"]
            if isinstance(j, dict) and j.get("error"):
                log.debug("rpc %s error via %s: %s", method, url, j["error"])
        except Exception as exc:  # noqa: BLE001
            log.debug("rpc %s via %s failed: %s", method, url, exc)
            continue
    return None


def _hexint(v, default: int = 0) -> int:
    try:
        return int(str(v), 16)
    except (TypeError, ValueError):
        return default


def block_number(endpoints) -> int:
    return _hexint(rpc(endpoints, "eth_blockNumber", []))


def native_balance(endpoints, address) -> int:
    return _hexint(rpc(endpoints, "eth_getBalance", [address, "latest"]))


def token_balance(endpoints, contract, address) -> int:
    data = "0x" + BALANCEOF_SELECTOR + address.lower().replace("0x", "").rjust(64, "0")
    return _hexint(rpc(endpoints, "eth_call", [{"to": contract, "data": data}, "latest"]))


def nonce(endpoints, address) -> int:
    return _hexint(rpc(endpoints, "eth_getTransactionCount", [address, "pending"]))


def gas_price(endpoints, floor: int, cap: int) -> int:
    gp = _hexint(rpc(endpoints, "eth_gasPrice", []), floor)
    return max(floor, min(gp, cap))


def get_logs_transfers(endpoints, contract, to_addresses, from_block, to_block):
    """eth_getLogs for ERC-20 Transfer events sending TO one of `to_addresses` on
    `contract`, across [from_block, to_block]. `to_addresses` may be a single address
    or a list (a list is one OR-filtered call — how per-order addresses are scanned).

    Returns (transfers, ok):
      transfers = [{txid, from, to, raw, block}], ok=False if the RPC failed — the
      caller must NOT advance its block cursor on ok=False, so nothing is ever skipped.
    Each log is RE-VERIFIED against the contract + topics — a malicious RPC can't
    forge a credit.
    """
    if isinstance(to_addresses, str):
        to_addresses = [to_addresses]
    topic_by_addr = {a.lower(): to_topic_address(a) for a in to_addresses}
    to_topics = list(topic_by_addr.values())
    addr_by_topic = {t.lower(): a for a, t in topic_by_addr.items()}
    params = [{
        "fromBlock": hex(int(from_block)),
        "toBlock": hex(int(to_block)),
        "address": contract,
        "topics": [EVM_TRANSFER_TOPIC, None, to_topics if len(to_topics) > 1 else to_topics[0]],
    }]
    res = rpc(endpoints, "eth_getLogs", params)
    if not isinstance(res, list):
        return [], False
    out = []
    for lg in res:
        try:
            topics = lg.get("topics") or []
            to_topic = (topics[2] or "").lower()
            if (lg.get("address", "").lower() != contract.lower()
                    or topics[0] != EVM_TRANSFER_TOPIC
                    or to_topic not in addr_by_topic):
                continue
            raw = _hexint(lg.get("data"))
            if raw <= 0:
                continue
            out.append({
                "txid": lg.get("transactionHash"),
                "from": "0x" + topics[1][-40:],
                "to": addr_by_topic[to_topic],
                "raw": raw,
                "block": _hexint(lg.get("blockNumber")),
            })
        except Exception:  # noqa: BLE001
            continue
    return out, True


# --- signing / broadcast (dedicated hot wallet only) ------------------------
def _sign_and_send(endpoints, priv, tx) -> str | None:
    from eth_account import Account
    signed = Account.sign_transaction(tx, priv)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    raw_hex = raw.hex() if hasattr(raw, "hex") else str(raw)
    if not raw_hex.startswith("0x"):
        raw_hex = "0x" + raw_hex
    return rpc(endpoints, "eth_sendRawTransaction", [raw_hex])


def send_native(endpoints, priv, to_addr, wei, gp, nonce_, chain_id, gas_limit=21_000):
    from eth_utils import to_checksum_address
    tx = {"to": to_checksum_address(to_addr), "value": int(wei), "gas": int(gas_limit),
          "gasPrice": int(gp), "nonce": int(nonce_), "chainId": int(chain_id)}
    return _sign_and_send(endpoints, priv, tx)


def send_token(endpoints, priv, contract, to_addr, amount, gp, nonce_, chain_id, gas_limit=90_000):
    from eth_utils import to_checksum_address
    data = ("0x" + TRANSFER_SELECTOR
            + to_addr.lower().replace("0x", "").rjust(64, "0")
            + format(int(amount), "x").rjust(64, "0"))
    tx = {"to": to_checksum_address(contract), "value": 0, "gas": int(gas_limit),
          "gasPrice": int(gp), "nonce": int(nonce_), "data": data, "chainId": int(chain_id)}
    return _sign_and_send(endpoints, priv, tx)
