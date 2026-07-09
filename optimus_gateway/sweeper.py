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
from .chains import CHAINS, EVM_METHODS, cents_divisor, chain_id, native_coin
from .config import config

log = logging.getLogger("optimus_gateway.sweeper")

# per-chain gas tuning (wei). ETH/Polygon token transfers are cheaper-gas than BSC's.
GAS = {
    56:  {"token": 90_000, "native": 21_000, "min": 1_000_000_000,  "max": 5_000_000_000},
    1:   {"token": 70_000, "native": 21_000, "min": 100_000_000,    "max": 60_000_000_000},
    137: {"token": 70_000, "native": 21_000, "min": 100_000_000,    "max": 600_000_000_000},
}


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
    need = len(balances) * g["token"] * gp
    if evm.native_balance(eps, addr) < need:
        if not tank_priv:
            return [], tank_nonce, "no_gas_tank"
        topup = int(need * 1.3)
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
    """Scan every per-order address on every EVM chain for USDT/USDC that landed there
    (any network). CREDIT the owning order (idempotent) and sweep to the main wallet.
    This is BOTH the wrong-network recovery AND the normal auto-sweep for per-order
    mode. Returns {status, credited, swept, scanned}."""
    dest = (config.sweep_destination() or "").strip()
    xprv = _xprv()
    if not xprv:
        return {"status": "no_key", "credited": [], "swept": []}
    if not dest:
        return {"status": "no_destination", "credited": [], "swept": []}
    rows = db.all_evm_order_addresses()
    credited, swept = [], []
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
        divisor = cents_divisor(method)
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
            if credit:
                for sym, contract, raw in bals:
                    cents = int(raw // divisor)
                    if cents <= 0:
                        continue
                    synth = "WRONGNET-%s-%s-%s" % (method.upper(), addr.lower(), contract.lower())
                    res = db.credit_by_address(method, addr, cents, synth)
                    if res.get("status") in ("paid", "partial", "topup"):
                        credited.append({"method": method, "address": addr, "token": sym,
                                         "amount": round(cents / 100.0, 2),
                                         "order_id": res.get("order_id"), "status": res.get("status")})
            sent, tank_nonce, st = _gas_up_and_sweep(
                method, xprv, int(idx), addr, bals, dest, gp, tank_priv, tank_addr, tank_nonce)
            swept.extend(sent)
    return {"status": "ok", "credited": credited, "swept": swept, "scanned": len(rows)}


# sweep_once is an alias — in per-order mode recovery already sweeps everything found.
def sweep_once() -> dict:
    if not config.auto_sweep():
        return {"status": "disabled"}
    return recover_wrongnet(credit=False)
