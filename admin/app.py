"""
Optimus Payment Gateway — optional admin dashboard (Flask).

A self-contained, read-mostly operator console:

  GET  /                 dashboard: order KPIs, enabled methods + config summary,
                         gas-tank balances per EVM chain, a recover button
  GET  /orders           filterable order table (?status=pending|paid|expired)
  GET  /order/<trade_id> single order detail (via gateway.get_payment)
  POST /recover          run sweeper.recover_wrongnet(credit=True) -> JSON
  GET  /health           lightweight JSON liveness probe (no auth, no secrets)

Security:
  * HTTP Basic auth on every operator page, using config.ADMIN_USERNAME /
    config.ADMIN_PASSWORD (constant-time compared).
  * If OPG_ADMIN_PASSWORD is empty the dashboard is DISABLED: `python -m admin.app`
    refuses to start, and any protected route returns 503 "admin disabled".
  * This process only READS the gateway (orders, gas-tank *balances*, config
    *summary*). It never reads, logs, renders or returns the receiving xpub, the
    sweep xprv, any private key, or any merchant/Binance secret. `config.summary()`
    and `sweeper.gas_tank_status()` are non-secret by construction.

Run: `python -m admin.app`  (reads OPG_ADMIN_PORT, default 8001; host 0.0.0.0).
"""
from __future__ import annotations

import functools
import hmac
import logging
import os
import sys

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

import optimus_gateway as opg
from optimus_gateway import config, db, gateway, sweeper
from optimus_gateway import chains  # read-only: chain labels/short names for display

log = logging.getLogger("optimus_gateway.admin")

# Cap the per-status scans used for the dashboard counters. This is an operator
# tool over one SQLite file; a few thousand rows is trivially fast.
_COUNT_LIMIT = 100_000
_STATUSES = (db.STATUS_PENDING, db.STATUS_PAID, db.STATUS_EXPIRED)

app = Flask(__name__)
# Not used for sessions (auth is stateless Basic), but harmless and available.
app.secret_key = config.ADMIN_SECRET_KEY
app.config["JSON_SORT_KEYS"] = False


# --------------------------------------------------------------------------- #
#  Auth
# --------------------------------------------------------------------------- #
def _admin_enabled() -> bool:
    """The dashboard is only enabled when an admin password is configured."""
    return bool(config.ADMIN_PASSWORD)


def _credentials_ok(auth) -> bool:
    """Constant-time check of a Basic-auth credential pair against the config."""
    if not auth or auth.username is None or auth.password is None:
        return False
    user_ok = hmac.compare_digest(
        (auth.username or "").encode("utf-8"),
        (config.ADMIN_USERNAME or "").encode("utf-8"),
    )
    pass_ok = hmac.compare_digest(
        (auth.password or "").encode("utf-8"),
        (config.ADMIN_PASSWORD or "").encode("utf-8"),
    )
    return user_ok and pass_ok


def _challenge() -> Response:
    return Response(
        "Authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="Optimus Gateway Admin"'},
    )


def _disabled(as_json: bool = False):
    msg = "admin disabled"
    if as_json:
        return jsonify({"ok": False, "error": msg}), 503
    return Response(msg, 503)


def requires_auth(view=None, *, as_json: bool = False):
    """Decorator: enforce that the dashboard is enabled AND the request carries
    valid Basic credentials. Returns 503 when disabled, 401 when unauthenticated."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not _admin_enabled():
                return _disabled(as_json=as_json)
            if not _credentials_ok(request.authorization):
                return _challenge()
            return fn(*args, **kwargs)
        return wrapper

    return decorator(view) if view else decorator


# --------------------------------------------------------------------------- #
#  Jinja display helpers (formatting only — no data access)
# --------------------------------------------------------------------------- #
@app.template_filter("usd")
def _fmt_usd(cents) -> str:
    """Integer cents -> '1234.56' (money is stored as cents everywhere)."""
    try:
        return f"{int(cents) / 100:,.2f}"
    except (TypeError, ValueError):
        return "0.00"


@app.template_filter("truncaddr")
def _truncaddr(addr, head: int = 10, tail: int = 8) -> str:
    if not addr:
        return "—"  # em dash
    addr = str(addr)
    if len(addr) <= head + tail + 1:
        return addr
    return f"{addr[:head]}…{addr[-tail:]}"


@app.template_filter("method_short")
def _method_short(method) -> str:
    return chains.CHAINS.get(method, {}).get("short", method or "—")


@app.template_filter("method_label")
def _method_label(method) -> str:
    return chains.CHAINS.get(method, {}).get("label", method or "—")


@app.context_processor
def _inject_globals() -> dict:
    return {"version": opg.__version__, "quote_currency": config.QUOTE_CURRENCY}


# --------------------------------------------------------------------------- #
#  Data assembly
# --------------------------------------------------------------------------- #
def _order_counts() -> dict:
    """Counts per status + total money actually received (settled) in cents."""
    counts = {}
    total_received_cents = 0
    pending_value_cents = 0
    for st in _STATUSES:
        rows = db.list_orders(status=st, limit=_COUNT_LIMIT)
        counts[st] = len(rows)
        if st == db.STATUS_PAID:
            total_received_cents = sum(int(r.get("received_cents") or 0) for r in rows)
        elif st == db.STATUS_PENDING:
            pending_value_cents = sum(int(r.get("expected_cents") or 0) for r in rows)
    counts["total"] = sum(counts[s] for s in _STATUSES)
    counts["total_received_cents"] = total_received_cents
    counts["pending_value_cents"] = pending_value_cents
    return counts


def _gas_tanks() -> tuple[list[dict], str | None]:
    """Enriched gas-tank rows for the dashboard, plus an optional error string.

    Never returns keys — sweeper.gas_tank_status() yields only {address, native,
    symbol}. RPC calls can be slow/unreachable, so failures degrade gracefully."""
    error = None
    try:
        raw = sweeper.gas_tank_status()  # {method: {address, native, symbol}}
    except Exception as exc:  # noqa: BLE001 — never let the dashboard 500 on RPCs
        log.warning("gas_tank_status failed: %s", exc)
        return [], str(exc)

    threshold = config.GAS_ALERT_THRESHOLD
    tanks = []
    for method, t in raw.items():
        address = t.get("address") or ""
        native = float(t.get("native") or 0)
        configured = bool(address)
        tanks.append({
            "method": method,
            "label": chains.CHAINS.get(method, {}).get("label", method),
            "address": address,
            "native": native,
            "symbol": t.get("symbol") or "",
            "configured": configured,
            "low": configured and native < threshold,
        })
    return tanks, error


# --------------------------------------------------------------------------- #
#  Routes
# --------------------------------------------------------------------------- #
@app.route("/")
@requires_auth
def dashboard():
    counts = _order_counts()
    tanks, gas_error = _gas_tanks()
    recent = db.list_orders(limit=8)
    return render_template(
        "dashboard.html",
        active="dashboard",
        counts=counts,
        tanks=tanks,
        gas_error=gas_error,
        gas_threshold=config.GAS_ALERT_THRESHOLD,
        summary=config.summary(),
        enabled_methods=config.ENABLED_METHODS,
        recent=recent,
    )


@app.route("/orders")
@requires_auth
def orders():
    status = (request.args.get("status") or "").strip().lower()
    if status not in _STATUSES:
        status = ""  # "all"
    rows = db.list_orders(status=status or None, limit=500)
    return render_template(
        "orders.html",
        active="orders",
        orders=rows,
        status=status,
        statuses=_STATUSES,
    )


@app.route("/order/<trade_id>")
@requires_auth
def order_detail(trade_id):
    order = gateway.get_payment(trade_id)
    if not order:
        return render_template("order_detail.html", active="orders",
                               order=None, trade_id=trade_id), 404
    return render_template("order_detail.html", active="orders",
                           order=order, trade_id=trade_id)


@app.route("/recover", methods=["POST"])
@requires_auth(as_json=True)
def recover():
    """Trigger one wrong-network scan + credit + sweep. Returns the sweeper's JSON
    result ({status, credited[], swept[], scanned}). Never returns keys."""
    try:
        result = sweeper.recover_wrongnet(credit=True)
        return jsonify({"ok": True, "result": result})
    except Exception as exc:  # noqa: BLE001
        log.exception("recover_wrongnet failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/health")
def health():
    """Public, secret-free liveness probe (works even while auth is required)."""
    return jsonify({
        "ok": True,
        "service": "optimus-admin",
        "version": opg.__version__,
        "admin_enabled": _admin_enabled(),
    })


# --------------------------------------------------------------------------- #
#  Entrypoint
# --------------------------------------------------------------------------- #
def create_app() -> Flask:
    """Return the WSGI app (for gunicorn/uwsgi: `admin.app:create_app()`)."""
    return app


def main() -> None:
    logging.basicConfig(
        level=os.getenv("OPG_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not _admin_enabled():
        sys.stderr.write(
            "[admin] OPG_ADMIN_PASSWORD is empty -> the admin dashboard is DISABLED.\n"
            "        Set OPG_ADMIN_PASSWORD (and optionally OPG_ADMIN_USER) to enable it.\n"
        )
        sys.exit(2)

    port = int(os.getenv("OPG_ADMIN_PORT", "8001"))
    opg.init()  # ensure the DB/schema exists (idempotent); admin only reads it.
    log.info("Optimus admin dashboard on http://0.0.0.0:%d (user=%s)",
             port, config.ADMIN_USERNAME)
    # threaded=True so a slow /recover or RPC call doesn't block the whole UI.
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
