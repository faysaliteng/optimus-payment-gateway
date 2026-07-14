"""
Sweeper — the money-out engine (optional; requires the dedicated hot-wallet xprv).

  * GAS TANK: index 0 of the dedicated wallet (the SAME address on every EVM chain).
    Fund it with a little native coin (BNB / ETH / POL) per chain; sweeps pay gas
    from it, topping up each per-order address just-in-time.
  * AUTO-SWEEP: forward incoming USDT/USDC from per-order addresses to your cold main
    wallet, so funds never sit on hot addresses.
  * WRONG-NETWORK RECOVERY: because every EVM chain shares one address space, a buyer
    who pays on the wrong network (e.g. USDC on Ethereum when you quoted BEP20) still
    sends to an address you control. This scans every per-order address on every EVM
    chain, CREDITS the order (idempotent), and sweeps it home. The BNB tank can't pay
    Ethereum gas, so each chain has its own native balance in the shared tank address.

Your MAIN wallet seed is never here. The sweeper only ever SENDS to the main address.
"""
from __future__ import annotations

import logging
import time

from . import db, evm, hdwallet
from .chains import CHAINS, EVM_METHODS, cents_divisor, chain_id, native_coin, is_real_stablecoin
from .config import config

log = logging.getLogger("optimus_gateway.sweeper")

# per-chain gas tuning (wei). Token-transfer gas LIMIT + a gas-price clamp band per
# chain. Arbitrum reports inflated L2 gas units (priced very low), so its limit is high;
# unused gas is refunded (the limit is a ceiling, you pay actual).
GAS = {
    # `token` is the gas LIMIT for a stablecoin sweep. It must exceed the transfer's real
    # gas use INCLUDING a cold-storage write (~20k extra) to a destination that holds none
    # of that token yet. USDT on Polygon/Ethereum can reach ~85-95k in that case, so a bare
    # 70k limit ran OUT OF GAS and the sweep reverted. 120k gives headroom — unused gas is
    # refunded (you only pay for gas actually used), so a generous limit is free insurance.
    56:    {"token": 90_000,    "native": 21_000,    "min": 1_000_000_000,   "max": 5_000_000_000},    # BSC
    1:     {"token": 120_000,   "native": 21_000,    "min": 100_000_000,     "max": 60_000_000_000},   # Ethereum
    137:   {"token": 120_000,   "native": 21_000,    "min": 100_000_000,     "max": 600_000_000_000},  # Polygon
    42161: {"token": 3_000_000, "native": 1_000_000, "min": 10_000_000,      "max": 20_000_000_000},   # Arbitrum
    10:    {"token": 300_000,   "native": 40_000,    "min": 1_000_000,       "max": 20_000_000_000},   # Optimism
    8453:  {"token": 300_000,   "native": 40_000,    "min": 1_000_000,       "max": 20_000_000_000},   # Base
    43114: {"token": 200_000,   "native": 30_000,    "min": 1_000_000_000,   "max": 300_000_000_000},  # Avalanche
}


def _log_forward(method: str, txid: str, amount_cents: int, from_addr: str, dest: str) -> None:
    """Record an outbound sweep (per-order address -> main/cold) so EVERY forward is
    visible for reconciliation. Idempotent by (method, txid). Never raises: sweep logging
    must not break a sweep."""
    if not txid or not method:
        return
    try:
        db.log_sweep(str(method), str(txid), int(amount_cents or 0),
                     str(from_addr or ""), str(dest or ""))
    except Exception:  # noqa: BLE001
        pass


def _xprv() -> str:
    return hdwallet.load_sweep_xprv(config.GATEWAY_SWEEP_KEY_PATH)


def _rpcs(method: str) -> list[str]:
    cfg = CHAINS[method]
    override = db.get_setting(cfg.get("rpc_setting", ""), "")
    custom = [u.strip() for u in override.replace("\n", ",").split(",")
              if u.strip().lower().startswith("http")]
    return custom + list(cfg["rpcs"])


def _tank_addr(xprv: str) -> str:
    return hdwallet.address_of_privkey(hdwallet.child_privkey(xprv, 0))


def _gas_price(eps, cid: int) -> int:
    g = GAS[cid]
    return evm.gas_price(eps, g["min"], g["max"])


def gas_tank_status() -> dict:
    """{method: {address, native, symbol}} — the gas tank's balance on each EVM chain."""
    xprv = _xprv()
    out = {}
    addr = _tank_addr(xprv) if xprv else ""
    for method in EVM_METHODS:
        eps = _rpcs(method)
        bal = evm.native_balance(eps, addr) if addr else 0
        out[method] = {"address": addr, "native": round(bal / 1e18, 6),
                       "symbol": native_coin(method)}
    return out


def _token_balances(eps, method: str, addr: str) -> list[tuple]:
    """[(symbol, contract, raw)] for every token with a balance at addr on this chain."""
    out = []
    for sym, contract in CHAINS[method]["tokens"].items():
        if sym != "USDT" and not config.accept_usdc():
            continue
        # Only ever touch known-real stablecoin contracts (fake-token guard).
        if not is_real_stablecoin(contract):
            continue
        b = evm.token_balance(eps, contract, addr)
        if b > 0:
            out.append((sym, contract, b))
    return out


def _gas_up_and_sweep(method, xprv, index, addr, balances, dest, gp, tank_priv, tank_addr,
                      tank_nonce) -> tuple:
    """Fund gas from the tank if needed (waits for confirm), then sweep each token to
    dest. Returns (sent, new_tank_nonce, status)."""
    eps = _rpcs(method)
    cid = chain_id(method)
    g = GAS[cid]
    # BUFFER the gas price 1.6x (capped at the chain's max). Volatile chains — Polygon
    # especially, whose gas can spike 3-10x its baseline — will underprice a sweep quoted
    # at the bare spot rate by the time it mines, so it never lands and the funds keep
    # resting (re-swept every pass). Overpaying gas a little is far cheaper than a stuck
    # sweep. Both the gas-need math and the actual txs use this buffered price.
    gp = min(int(gp * 1.6), g["max"])
    need = len(balances) * g["token"] * gp
    if evm.native_balance(eps, addr) < need:
        if not tank_priv:
            return [], tank_nonce, "no_gas_tank"
        topup = int(need * 1.5)  # was 1.3 — extra headroom so a gas tick up can't underfund the sweep
        if evm.native_balance(eps, tank_addr) < topup + g["native"] * gp:
            return [], tank_nonce, "gas_tank_low"
        if not evm.send_native(eps, tank_priv, addr, topup, gp, tank_nonce, cid, g["native"]):
            return [], tank_nonce, "gas_send_failed"
        tank_nonce = (tank_nonce or 0) + 1
        for _ in range(30):  # ~60s
            time.sleep(2)
            if evm.native_balance(eps, addr) >= need:
                break
        else:
            return [], tank_nonce, "gas_pending"
    priv = hdwallet.child_privkey(xprv, index)
    nonce = evm.nonce(eps, addr)
    sent = []
    for sym, contract, raw in balances:
        txid = evm.send_token(eps, priv, contract, dest, raw, gp, nonce, cid, g["token"])
        if txid:
            sent.append({"method": method, "address": addr, "token": sym,
                         "amount": round(raw / (cents_divisor(method) * 100), 6),
                         "txid": txid, "explorer": CHAINS[method]["explorer"]})
            nonce += 1
    return sent, tank_nonce, ("ok" if sent else "send_failed")


def recover_wrongnet(credit: bool = True) -> dict:
    """SWEEP-ONLY money-out. Scan every per-order address on every EVM chain and forward
    any USDT/USDC sitting there (including WRONG-NETWORK deposits) home to the cold wallet.

    CREDITING is NOT done here — the watcher credits every deposit to a per-order address
    on any EVM chain by its real (txid, logIndex), idempotently (see
    db.all_active_order_addresses). Keeping credit in one place removes the whole class of
    balance-based double-credit / stale-balance / equal-amount-collision bugs. The
    `credit` argument is retained for API/CLI compatibility and is ignored.

    Note: a deposit that only arrives AFTER its order has expired (past the reservation +
    cooldown window the watcher scans) is still swept safely to cold storage, but is not
    auto-credited to the order — reconcile that rare case manually.
    Returns {status, swept, scanned}."""
    dest = (config.sweep_destination() or "").strip()
    xprv = _xprv()
    if not xprv:
        return {"status": "no_key", "swept": []}
    if not dest:
        return {"status": "no_destination", "swept": []}
    rows = db.all_evm_order_addresses()
    swept = []
    for method in EVM_METHODS:
        eps = _rpcs(method)
        cid = chain_id(method)
        gp = _gas_price(eps, cid)
        try:
            tank_priv = hdwallet.child_privkey(xprv, 0)
            tank_addr = hdwallet.address_of_privkey(tank_priv)
            tank_nonce = evm.nonce(eps, tank_addr)
        except Exception:  # noqa: BLE001
            tank_priv = tank_addr = None
            tank_nonce = 0
        for r in rows:
            idx = r.get("address_index")
            addr = r.get("pay_address")
            if idx is None or not addr:
                continue
            bals = _token_balances(eps, method, addr)
            if not bals:
                continue
            # safety: confirm we actually control this derived address
            if hdwallet.address_of_privkey(hdwallet.child_privkey(xprv, int(idx))).lower() != addr.lower():
                continue
            sent, tank_nonce, st = _gas_up_and_sweep(
                method, xprv, int(idx), addr, bals, dest, gp, tank_priv, tank_addr, tank_nonce)
            if not sent:
                continue
            # CONFIRM each sweep actually LANDED before reporting it. A returned txid only
            # means the tx was broadcast — gas volatility can leave it pending or revert it,
            # so the funds never move. Re-read the token balance after a short wait; only
            # count a token as swept once its balance has actually dropped. An unconfirmed
            # one is simply retried on the next pass (idempotent), and callers/notifications
            # are never told funds "moved" when they didn't.
            time.sleep(12)
            tokens_map = CHAINS[method]["tokens"]
            confirmed_any = False
            for s in sent:
                contract = tokens_map.get(s["token"])
                if contract is None or evm.token_balance(eps, contract, addr) <= 10_000:
                    swept.append(s)
                    confirmed_any = True
                    # ALL-SWEEPS-LOGGING: record every confirmed forward (per-order address
                    # -> cold main) so the money-out is auditable. `amount` is in whole
                    # tokens (USDT/USDC ~= USD); convert to cents.
                    _log_forward(method, s.get("txid", ""),
                                 int(round(float(s.get("amount") or 0) * 100)), addr, dest)
            if confirmed_any:
                # De-dup the accumulating pool: one address holds several orders' coins, so
                # stamp EVERY funded row on it swept in one shot (idempotent).
                try:
                    db.mark_address_swept(addr, sent[-1].get("txid", ""))
                except Exception:  # noqa: BLE001
                    pass
    return {"status": "ok", "swept": swept, "scanned": len(rows)}


# sweep_once — same sweep-only pass (the watcher already credited everything).
def sweep_once() -> dict:
    if not config.auto_sweep():
        return {"status": "disabled"}
    return recover_wrongnet()
