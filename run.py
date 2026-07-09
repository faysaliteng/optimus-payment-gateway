#!/usr/bin/env python
"""
Optimus Payment Gateway — entrypoint.

    python run.py                 # start the API + watcher + sweeper
    python run.py serve           # same
    python run.py newwallet       # generate a dedicated hot wallet (for auto-sweep)
    python run.py checkxpub XPUB  # validate a watch-only xpub + show first addresses
    python run.py recover         # one-shot: credit + sweep any wrong-network funds
    python run.py tanks           # show gas-tank balances per chain
"""
from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(level=os.getenv("OPG_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _serve():
    import uvicorn
    from optimus_gateway import config
    uvicorn.run("server.app:app", host=config.HOST, port=config.PORT, log_level="info")


def _newwallet():
    from optimus_gateway import hdwallet, config
    w = hdwallet.generate_dedicated_wallet()
    print("\n=== NEW DEDICATED GATEWAY WALLET ===")
    print("Mnemonic (WRITE THIS DOWN OFFLINE, then delete from screen):")
    print("   " + w["mnemonic"])
    print("\nAccount xpub (RECEIVING key — put in OPG_GATEWAY_XPUB):")
    print("   " + w["account_xpub"])
    print("\nGas tank / index-0 address (fund with a little native coin per chain):")
    print("   " + w["address_0"])
    path = config.GATEWAY_SWEEP_KEY_PATH
    ans = input(f"\nSave the spend key (xprv) to {path} (0600) to enable AUTO-SWEEP? [y/N] ")
    if ans.strip().lower().startswith("y"):
        hdwallet.save_sweep_xprv(path, w["account_xprv"])
        print(f"Saved. Keep {path} secret; it can move funds from gateway addresses.")
    else:
        print("Skipped. Watch-only mode: the gateway will credit deposits but not sweep.")


def _checkxpub(xpub):
    from optimus_gateway import hdwallet
    r = hdwallet.validate_xpub(xpub)
    if not r.get("ok"):
        print("INVALID:", r.get("error"))
        return
    print("OK — watch-only xpub.")
    for i in range(1, 6):
        print(f"  index {i}: {hdwallet.address_from_xpub(xpub, i)}")


def _recover():
    from optimus_gateway import sweeper, init as _init
    _init()
    res = sweeper.recover_wrongnet(credit=True)
    print("recover:", res.get("status"), "| credited:", len(res.get("credited", [])),
          "| swept:", len(res.get("swept", [])))
    for c in res.get("credited", []):
        print("  credited", c)
    for s in res.get("swept", []):
        print("  swept", s)


def _tanks():
    from optimus_gateway import sweeper, init as _init
    _init()
    for method, t in sweeper.gas_tank_status().items():
        print(f"  {method:14s} {t['address']}  {t['native']:.6f} {t['symbol']}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd in ("serve", "run"):
        _serve()
    elif cmd == "newwallet":
        _newwallet()
    elif cmd == "checkxpub" and len(sys.argv) > 2:
        _checkxpub(sys.argv[2])
    elif cmd == "recover":
        _recover()
    elif cmd == "tanks":
        _tanks()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
