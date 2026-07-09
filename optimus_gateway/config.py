"""
Configuration — 12-factor style. Everything is read from environment variables
(with sane defaults), so the gateway runs identically in dev, Docker, and prod.

Secrets policy (READ THIS):
  * The receiving XPUB is watch-only and safe to keep here / in the DB.
  * The gateway hot-wallet XPRV (used to auto-sweep) is loaded from a locked file
    (GATEWAY_SWEEP_KEY_PATH, chmod 600) — NEVER from an env var committed to git,
    NEVER logged, NEVER stored in the DB.
  * The main wallet seed (where swept funds land) is NEVER on the server. The
    sweeper only ever SENDS to the main address; it can't spend it.
"""
from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on", "y"}


def _b(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    return default if v is None else v.strip().lower() in _TRUE


def _i(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _f(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


class Config:
    # ---- core ----
    DB_PATH = os.getenv("OPG_DB_PATH", "optimus_gateway.db")
    HOST = os.getenv("OPG_HOST", "0.0.0.0")
    PORT = _i("OPG_PORT", 8000)
    BASE_URL = os.getenv("OPG_BASE_URL", "http://localhost:8000").rstrip("/")

    # Pricing currency the merchant quotes in (fiat). Amounts are converted to the
    # stablecoin 1:1 for USD; add an FX source for other fiats.
    QUOTE_CURRENCY = os.getenv("OPG_QUOTE_CURRENCY", "USD").upper()

    # ---- receiving wallet (HD) ----
    # Watch-only account xpub (m/44'/60'/0'/0). Per-order addresses are children of
    # this. Keep the matching seed OFFLINE. Required for per-order-address mode.
    GATEWAY_XPUB = os.getenv("OPG_GATEWAY_XPUB", "").strip()
    # Shared receiving address for amount-matching mode (index 0 / your main wallet).
    SHARED_RECEIVE_ADDRESS = os.getenv("OPG_SHARED_RECEIVE_ADDRESS", "").strip()

    # ---- sweeping (optional, auto-forward to a cold main wallet) ----
    GATEWAY_SWEEP_KEY_PATH = os.getenv("OPG_SWEEP_KEY_PATH", "private/gateway_sweep/account.xprv")
    SWEEP_DESTINATION = os.getenv("OPG_SWEEP_DESTINATION", "").strip()  # your main wallet
    AUTO_SWEEP_ENABLED = _b("OPG_AUTO_SWEEP", False)

    # ---- which networks are enabled (comma list of method keys) ----
    ENABLED_METHODS = [
        m.strip() for m in os.getenv(
            "OPG_ENABLED_METHODS", "usdt_bep20"
        ).split(",") if m.strip()
    ]
    ACCEPT_USDC = _b("OPG_ACCEPT_USDC", True)

    # ---- watcher tuning ----
    MIN_CONFIRMATIONS = max(1, min(50, _i("OPG_MIN_CONFIRMATIONS", 3)))
    RESCAN_OVERLAP = _i("OPG_RESCAN_OVERLAP", 24)
    MAX_CATCHUP_BLOCKS = _i("OPG_MAX_CATCHUP_BLOCKS", 1500)
    WATCH_POLL_SECONDS = max(10, _i("OPG_WATCH_POLL_SECONDS", 20))
    RESERVATION_TTL_MINUTES = max(5, min(240, _i("OPG_RESERVATION_TTL_MINUTES", 40)))
    AMOUNT_COOLDOWN_MINUTES = _i("OPG_AMOUNT_COOLDOWN_MINUTES", 1440)  # 24h late-payment window

    # ---- sweep tuning ----
    SWEEP_POLL_SECONDS = _i("OPG_SWEEP_POLL_SECONDS", 120)
    WRONGNET_POLL_SECONDS = _i("OPG_WRONGNET_POLL_SECONDS", 900)
    GAS_ALERT_THRESHOLD = _f("OPG_GAS_ALERT_THRESHOLD", 0.005)

    # ---- Binance verification (optional; personal read-only API key) ----
    BINANCE_ENABLED = _b("OPG_BINANCE_ENABLED", False)
    BINANCE_API_KEY = os.getenv("OPG_BINANCE_API_KEY", "").strip()
    BINANCE_API_SECRET = os.getenv("OPG_BINANCE_API_SECRET", "").strip()
    BINANCE_BASE_URL = os.getenv("OPG_BINANCE_BASE_URL", "https://api.binance.com").rstrip("/")
    BINANCE_AMOUNT_TOLERANCE = _f("OPG_BINANCE_AMOUNT_TOLERANCE", 0.50)

    # ---- TON ----
    TON_RECEIVE_ADDRESS = os.getenv("OPG_TON_ADDRESS", "").strip()
    TONCENTER_API_KEY = os.getenv("OPG_TONCENTER_API_KEY", "").strip()

    # ---- merchant API auth + webhook signing ----
    # Merchants authenticate create-order calls and verify our webhooks with these.
    MERCHANT_API_KEY = os.getenv("OPG_MERCHANT_API_KEY", "").strip()
    MERCHANT_API_SECRET = os.getenv("OPG_MERCHANT_API_SECRET", "").strip()
    WEBHOOK_MAX_RETRIES = _i("OPG_WEBHOOK_MAX_RETRIES", 6)
    WEBHOOK_TIMEOUT = _i("OPG_WEBHOOK_TIMEOUT", 12)

    # ---- admin dashboard ----
    ADMIN_USERNAME = os.getenv("OPG_ADMIN_USER", "admin")
    ADMIN_PASSWORD = os.getenv("OPG_ADMIN_PASSWORD", "")  # empty = admin disabled
    ADMIN_SECRET_KEY = os.getenv("OPG_ADMIN_SECRET_KEY", os.urandom(24).hex())

    # ------------------------------------------------------------------
    #  LIVE config — the "hot" settings are read from the DB first (so the
    #  admin Setup Wizard can change them with no restart), falling back to the
    #  env var above. Bootstrap/security values (DB_PATH, HOST/PORT, admin creds,
    #  merchant secret, sweep KEY PATH) stay env-only. The DB read is lazy to
    #  avoid a config<->db import cycle.
    # ------------------------------------------------------------------
    def _s(self, db_key: str, env_default: str) -> str:
        from . import db
        try:
            v = db.get_setting(db_key, None)
        except Exception:
            v = None
        return v if v not in (None, "") else env_default

    def _sb(self, db_key: str, env_default: bool) -> bool:
        v = self._s(db_key, None)
        return env_default if v is None else str(v).strip().lower() in _TRUE

    def xpub(self) -> str:
        return self._s("gateway_xpub", self.GATEWAY_XPUB)

    def shared_address(self) -> str:
        return self._s("shared_receive_address", self.SHARED_RECEIVE_ADDRESS)

    def sweep_destination(self) -> str:
        return self._s("sweep_destination", self.SWEEP_DESTINATION)

    def ton_address(self) -> str:
        return self._s("ton_address", self.TON_RECEIVE_ADDRESS)

    def toncenter_key(self) -> str:
        return self._s("toncenter_api_key", self.TONCENTER_API_KEY)

    def auto_sweep(self) -> bool:
        return self._sb("auto_sweep", self.AUTO_SWEEP_ENABLED)

    def accept_usdc(self) -> bool:
        return self._sb("accept_usdc", self.ACCEPT_USDC)

    def enabled_methods(self) -> list[str]:
        raw = self._s("enabled_methods", ",".join(self.ENABLED_METHODS))
        return [m.strip() for m in str(raw).split(",") if m.strip()]

    def binance_enabled(self) -> bool:
        return self._sb("binance_enabled", self.BINANCE_ENABLED) and bool(self.BINANCE_API_KEY)

    def is_configured(self) -> bool:
        """True once a receiving wallet (xpub or shared address) is set — the wizard
        uses this to show the setup prompt until it's done."""
        return bool(self.xpub() or self.shared_address())

    def summary(self) -> dict:
        """Non-secret snapshot for /health, the admin UI, and logs."""
        methods = self.enabled_methods()
        return {
            "configured": self.is_configured(),
            "base_url": self.BASE_URL,
            "quote_currency": self.QUOTE_CURRENCY,
            "enabled_methods": methods,
            "accept_usdc": self.accept_usdc(),
            "per_order_address_mode": bool(self.xpub()),
            "amount_match_mode": bool(self.shared_address()) and not bool(self.xpub()),
            "auto_sweep": self.auto_sweep(),
            "sweep_destination_set": bool(self.sweep_destination()),
            "sweep_key_present": bool(self.GATEWAY_SWEEP_KEY_PATH and __import__("os").path.exists(self.GATEWAY_SWEEP_KEY_PATH)),
            "binance_verify": self.binance_enabled(),
            "ton_enabled": "usdt_ton" in methods and bool(self.ton_address()),
            "min_confirmations": self.MIN_CONFIRMATIONS,
            "reservation_ttl_minutes": self.RESERVATION_TTL_MINUTES,
        }


config = Config()
