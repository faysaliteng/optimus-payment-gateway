"""
Background workers — three daemon threads that make the gateway autonomous:

  watcher loop  : scan chains, credit paid orders, fire on_paid -> webhook queue
  sweeper loop  : (optional) auto-forward funds to the cold main wallet + wrong-network
  webhook loop  : deliver queued merchant callbacks with retries

Threads (not asyncio) because the chain RPC/HTTP calls are blocking; each loop is
self-throttling and swallows its own errors so one bad tick never kills the service.
"""
from __future__ import annotations

import logging
import threading
import time

from optimus_gateway import config
from optimus_gateway import watcher, sweeper, webhook

log = logging.getLogger("optimus_gateway.workers")

_started = False


def _loop(name: str, fn, interval: int):
    while True:
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("%s tick failed", name)
        time.sleep(interval)


def _watch_tick():
    res = watcher.scan_all(on_paid=webhook.on_paid)
    paid = sum(r.get("credited", 0) for r in res.values() if isinstance(r, dict))
    if paid:
        log.info("watcher: %s order(s) paid this tick", paid)


def _sweep_tick():
    if not config.auto_sweep():          # live-toggleable from the Setup wizard
        return
    res = sweeper.recover_wrongnet(credit=True)
    if res.get("credited") or res.get("swept"):
        log.info("sweeper: credited=%s swept=%s", len(res.get("credited", [])),
                 len(res.get("swept", [])))


def _webhook_tick():
    webhook.deliver_due(limit=25)


def start_background() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, args=("watcher", _watch_tick, config.WATCH_POLL_SECONDS),
                     daemon=True).start()
    threading.Thread(target=_loop, args=("webhook", _webhook_tick, 5), daemon=True).start()
    # Always run the sweeper thread; each tick checks the live auto_sweep() flag, so
    # you can turn sweeping on/off from the Setup wizard without a restart.
    threading.Thread(target=_loop, args=("sweeper", _sweep_tick, config.WRONGNET_POLL_SECONDS),
                     daemon=True).start()
    log.info("background workers started (%s)", config.summary())
