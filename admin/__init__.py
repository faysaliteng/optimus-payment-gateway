"""
Optional operator dashboard for the Optimus Payment Gateway.

A tiny, read-mostly Flask app (Flask + Jinja only) that lets an operator watch
payments, gas-tank balances and wrong-network recovery from a browser. It is a
thin VIEW over the `optimus_gateway` package — it never touches keys and never
runs the watcher/sweeper loops itself.

Run it standalone:

    python -m admin.app        # binds 0.0.0.0:OPG_ADMIN_PORT (default 8001)

Auth is HTTP Basic using OPG_ADMIN_USER / OPG_ADMIN_PASSWORD. If the password is
empty the dashboard is DISABLED (refuses to start / returns 503).
"""
