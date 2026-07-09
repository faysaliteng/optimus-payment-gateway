"""
Optimus Payment Gateway — a self-hosted, non-custodial, multi-chain crypto payment
gateway. Accept USDT/USDC on BSC (BEP20), Ethereum (ERC20), Polygon and TON with
per-order HD addresses (watch-only xpub), automatic gas-tank sweeping to a cold main
wallet, wrong-network recovery, optional Binance verification, and signed merchant
webhooks — all without a third-party processor holding your funds.

Quick start (library):

    from optimus_gateway import init, create_payment, get_payment
    init()
    order = create_payment("usdt_bep20", 25.00, merchant_order_id="INV-1",
                           notify_url="https://shop/hook")
    print(order["pay_address"], order["pay_amount"])

Run the full service (REST API + watcher + sweeper): `python run.py`.
"""
from .config import config
from .db import init_db
from .gateway import create_payment, get_payment

__version__ = "1.0.0"
__all__ = ["init", "config", "create_payment", "get_payment"]


def init() -> None:
    """Initialise the database (idempotent). Call once at startup."""
    init_db()
