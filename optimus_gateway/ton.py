"""
TON (USDT jetton) helpers — address conversion + toncenter transfer polling.

TON has no per-order address concept like EVM; instead it carries a native text
COMMENT on transfers. So each order gets a unique memo, the payer sends any amount
to your single TON wallet WITH that memo, and we route the credit by comment.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.request

from .chains import CHAINS

log = logging.getLogger("optimus_gateway.ton")

_MASTER = CHAINS["usdt_ton"]["jetton_master"]
_TONCENTER = CHAINS["usdt_ton"]["toncenter"]


def address_to_raw(friendly: str) -> str | None:
    """Convert a user-friendly EQ…/UQ… TON address to raw 'workchain:hex'."""
    try:
        s = friendly.strip().replace("-", "+").replace("_", "/")
        raw = base64.b64decode(s + "=" * (-len(s) % 4))
        if len(raw) < 34:
            return None
        wc = raw[1]
        wc = wc - 256 if wc > 127 else wc
        return f"{wc}:{raw[2:34].hex()}"
    except Exception:  # noqa: BLE001
        return None


def fetch_incoming(receive_address: str, api_key: str = "", limit: int = 30) -> list[dict]:
    """Poll toncenter v3 for INCOMING USDT-jetton transfers to `receive_address`.
    Returns [{txid, from, raw, comment, ts}]."""
    raw = address_to_raw(receive_address)
    if not raw:
        return []
    url = f"{_TONCENTER}/jetton/transfers?owner_address={raw}&direction=in&limit={int(limit)}"
    headers = {"Accept": "application/json", "User-Agent": "OptimusGateway/1.0"}
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.load(r)
    except Exception as exc:  # noqa: BLE001
        log.debug("toncenter fetch failed: %s", exc)
        return []
    out = []
    for row in (body.get("jetton_transfers") or []):
        try:
            if (row.get("jetton_master") or "").lower() != _MASTER.lower():
                continue
            if row.get("transaction_aborted"):
                continue
            amt = int(row.get("amount") or 0)
            if amt <= 0:
                continue
            comment = ((row.get("decoded_forward_payload") or {}) or {}).get("comment")
            out.append({
                "txid": row.get("transaction_hash"),
                "from": row.get("source"),
                "raw": amt,
                "comment": (comment or "").strip(),
                "ts": row.get("transaction_now"),
            })
        except Exception:  # noqa: BLE001
            continue
    return out
