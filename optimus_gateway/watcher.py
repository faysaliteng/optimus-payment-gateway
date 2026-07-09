"""
The watcher — the money-in engine. It scans each enabled chain for incoming
stablecoin transfers and credits the owning order idempotently.

EVM (BSC / Ethereum / Polygon): a per-chain block cursor advances ONLY over blocks
actually scanned (never past unscanned blocks, never on an RPC error), with a small
re-scan overlap. So a crash or a slow node can never make it skip a payment; the
worst case is a re-scan, which the reference-registry makes harmless.

TON: polls toncenter for incoming USDT-jetton transfers and routes each by its text
comment (the per-order memo).

When an order becomes fully paid, on_paid(order) fires — the server uses that to
enqueue the merchant webhook.
"""
from __future__ import annotations

import logging

from . import db, evm, ton
from .chains import CHAINS, cents_divisor, to_cents, is_real_stablecoin
from .config import config

log = logging.getLogger("optimus_gateway.watcher")


def _rpcs(method: str) -> list[str]:
    cfg = CHAINS[method]
    override = db.get_setting(cfg.get("rpc_setting", ""), "")
    custom = [u.strip() for u in override.replace("\n", ",").split(",")
              if u.strip().lower().startswith("http")]
    return custom + list(cfg["rpcs"])


def _watched_tokens(method: str) -> dict:
    cfg = CHAINS[method]
    toks = {"USDT": cfg["tokens"]["USDT"]}
    if config.accept_usdc():
        for k, v in cfg["tokens"].items():
            if k != "USDT":
                toks[k] = v
    # Fake-token guard (defense-in-depth): only ever scan/credit KNOWN-REAL stablecoin
    # contracts. getLogs is already filtered by these exact contracts, so a scam token
    # sent to a gateway address is structurally never scanned — this makes it explicit
    # and immune to a stray bad entry ever reaching the registry.
    return {sym: c for sym, c in toks.items() if is_real_stablecoin(c)}


def scan_evm(method: str, on_paid=None) -> dict:
    cfg = CHAINS[method]
    eps = _rpcs(method)
    latest = evm.block_number(eps)
    if latest <= 0:
        return {"ok": False, "reason": "no_rpc"}
    confirmed_to = latest - config.confirmations(method)
    if confirmed_to <= 0:
        return {"ok": True, "credited": 0}

    cursor_key = cfg["cursor_key"]
    last = int(db.get_setting(cursor_key, "0") or 0)
    if last <= 0:
        last = max(1, confirmed_to - int(cfg["initial_lookback"]))
    from_block = max(1, last + 1 - config.RESCAN_OVERLAP)
    if confirmed_to < from_block:
        return {"ok": True, "credited": 0}
    # Per-chain catch-up cap: fragile chains (e.g. Polygon, whose public nodes cap
    # getLogs at ~20-block ranges and rate-limit rapid calls) set a small "max_catchup"
    # so one cycle never fires more calls than the RPCs tolerate; others use the global
    # default. The cursor still advances only over blocks actually scanned, so a capped
    # cycle just means the rest is picked up next cycle — no payment is ever skipped.
    max_catchup = int(cfg.get("max_catchup", config.MAX_CATCHUP_BLOCKS))
    scan_to = min(confirmed_to, from_block + max_catchup - 1)

    # what to watch: per-order addresses (xpub mode) or the shared address (amount-match)
    per_order = bool(config.xpub())
    if per_order:
        # Watch EVERY active per-order address across ALL methods — not just this chain's
        # orders — so a WRONG-NETWORK payment (buyer paid on a different EVM chain than
        # quoted) is credited here by its real (txid, logIndex), idempotently. This makes
        # the watcher the single crediting path; the sweeper only moves funds.
        actives = db.all_active_order_addresses()
        to_addresses = sorted({a["pay_address"] for a in actives if a.get("pay_address")})
        if not to_addresses:
            # nothing to watch yet — still advance the cursor so we don't re-scan forever
            db.set_setting(cursor_key, str(scan_to))
            return {"ok": True, "credited": 0}
    else:
        shared = config.shared_address()
        if not shared:
            return {"ok": False, "reason": "no_receiver"}
        to_addresses = [shared]

    divisor = cents_divisor(method)
    # Scan ALL of the chain's watched stablecoins in ONE getLogs call per block-chunk
    # (eth_getLogs `address` accepts an array). This keeps the per-cycle call count at
    # (blocks / max_span) x address-chunks — independent of how many tokens are watched —
    # so a rate-limited public node (Polygon) isn't hit ~3x as hard, which would trip its
    # limit mid-catch-up and, with the all-or-nothing cursor advance below, stall it.
    contracts = list(_watched_tokens(method).values())
    if not contracts:
        # no real stablecoin to watch (shouldn't happen — USDT is always real) — advance
        # the cursor so we don't re-scan forever.
        db.set_setting(cursor_key, str(scan_to))
        return {"ok": True, "credited": 0}
    credited = 0
    span = int(cfg["max_span"])
    ok_all = True
    start = from_block
    while start <= scan_to:
        end = min(start + span - 1, scan_to)
        # OR-filter over up to ~1000 addresses per call is fine; chunk to be safe
        for addr_chunk in _chunks(to_addresses, 400):
            transfers, ok = evm.get_logs_transfers(eps, contracts, addr_chunk, start, end)
            if not ok:
                ok_all = False
                break
            for t in transfers:
                whole_cent = (t["raw"] % divisor == 0)
                cents = t["raw"] // divisor
                if cents <= 0:
                    continue
                # (txid, logIndex) keys the idempotency registry so a batch/multisend
                # tx with several Transfer events credits each one, not just the first.
                suffix = str(t.get("log_index", 0))
                if per_order:
                    res = db.credit_by_address(method, t["to"], cents, t["txid"], suffix)
                else:
                    if not whole_cent:
                        continue  # amount-match only trusts whole-cent amounts
                    res = db.credit_by_amount(method, cents, t["txid"], suffix)
                if res.get("status") == "paid":
                    credited += 1
                    _fire_paid(res["order_id"], on_paid)
                elif res.get("status") in ("partial", "topup"):
                    log.info("%s partial credit order=%s +%d cents", method,
                             res.get("order_id"), cents)
        if not ok_all:
            break
        start = end + 1

    if ok_all:
        db.set_setting(cursor_key, str(scan_to))
    return {"ok": ok_all, "credited": credited, "scanned_to": scan_to if ok_all else last}


def scan_ton(method: str = "usdt_ton", on_paid=None) -> dict:
    transfers = ton.fetch_incoming(config.ton_address(), config.toncenter_key())
    divisor = cents_divisor(method)
    credited = 0
    for t in transfers:
        cents = int(t["raw"]) // divisor
        if cents <= 0 or not t.get("comment"):
            continue
        res = db.credit_by_memo(method, t["comment"], cents, t["txid"])
        if res.get("status") == "paid":
            credited += 1
            _fire_paid(res["order_id"], on_paid)
    return {"ok": True, "credited": credited}


def scan_all(on_paid=None) -> dict:
    db.expire_orders()
    out = {}
    for method in config.enabled_methods():
        cfg = CHAINS.get(method)
        if not cfg:
            continue
        try:
            if cfg["scanner"] == "evm":
                out[method] = scan_evm(method, on_paid)
            elif cfg["scanner"] == "ton_memo":
                out[method] = scan_ton(method, on_paid)
        except Exception:  # noqa: BLE001
            log.exception("scan %s failed", method)
            out[method] = {"ok": False, "reason": "exception"}
    return out


def _fire_paid(order_id: int, on_paid):
    order = db.get_order(order_id=order_id)
    if order and callable(on_paid):
        try:
            on_paid(order)
        except Exception:  # noqa: BLE001
            log.exception("on_paid callback failed for order %s", order_id)


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
