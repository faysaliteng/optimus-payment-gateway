"""
Storage layer (SQLite, WAL) — orders, the anti-replay reference registry, HD address
allocation, settings/cursors, and the webhook delivery queue.

Money invariants enforced here:
  * Every amount is stored as integer CENTS. No floats in the ledger.
  * A txid/reference is CLAIMED in payment_reference_registry (a PRIMARY-KEY table)
    inside the same transaction that credits — so a re-scan can never double-credit.
  * credit-not-consume: any amount that arrives to an order's address/memo is
    accumulated; the order flips to PAID only once the running total covers the
    expected amount. Overpayment is recorded, not lost.
  * Reservations are BEGIN IMMEDIATE transactions so two workers can't race.
"""
from __future__ import annotations

import json
import sqlite3
import time

from .config import config
from .security import normalize_reference

STATUS_PENDING = "pending"
STATUS_PAID = "paid"
STATUS_EXPIRED = "expired"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE,               -- our public id
    merchant_order_id TEXT,             -- merchant's own order id (idempotency)
    method TEXT NOT NULL,               -- usdt_bep20 / usdt_erc20 / usdt_polygon / usdt_ton
    quote_amount REAL,                  -- amount the merchant quoted (fiat)
    quote_currency TEXT,
    expected_cents INTEGER NOT NULL,    -- exact amount to pay, in cents
    received_cents INTEGER DEFAULT 0,   -- running total credited
    address_index INTEGER,              -- HD index (per-order-address mode)
    pay_address TEXT,                   -- receiving address
    pay_memo TEXT,                      -- TON comment (memo mode)
    status TEXT DEFAULT 'pending',
    notify_url TEXT,
    redirect_url TEXT,
    metadata TEXT,                      -- opaque merchant JSON
    tx_hashes TEXT DEFAULT '',          -- CSV of on-chain txids seen
    swept_at TEXT,
    reservation_expires_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    paid_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_method_status ON orders(method, status);
CREATE INDEX IF NOT EXISTS idx_orders_amount ON orders(method, expected_cents, status);
CREATE INDEX IF NOT EXISTS idx_orders_address ON orders(pay_address);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_merchant ON orders(merchant_order_id) WHERE merchant_order_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS payment_reference_registry (
    normalized_reference TEXT PRIMARY KEY,
    original_reference TEXT,
    reference_type TEXT,
    order_id INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS address_counter (
    method TEXT PRIMARY KEY,
    next_index INTEGER DEFAULT 1        -- index 0 is the main wallet / gas tank
);

CREATE TABLE IF NOT EXISTS webhook_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    url TEXT,
    payload TEXT,
    attempts INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',      -- pending / delivered / failed
    next_attempt_at REAL DEFAULT 0,
    last_error TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_webhook_pending ON webhook_queue(status, next_attempt_at);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# --- settings / cursors -----------------------------------------------------
def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r and r["value"] is not None else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        conn.commit()
    finally:
        conn.close()


# --- HD address allocation --------------------------------------------------
def next_address_index(conn: sqlite3.Connection, method: str) -> int:
    conn.execute("INSERT OR IGNORE INTO address_counter(method,next_index) VALUES(?,1)", (method,))
    row = conn.execute("SELECT next_index FROM address_counter WHERE method=?", (method,)).fetchone()
    idx = int(row["next_index"])
    conn.execute("UPDATE address_counter SET next_index=? WHERE method=?", (idx + 1, method))
    return idx


# --- reservation ------------------------------------------------------------
def _unique_amount_cents(conn: sqlite3.Connection, method: str, base_cents: int) -> int:
    """Amount-match mode: find the smallest cents value >= base_cents not currently
    'taken' by an active/cooldown reservation for this method (so the on-chain amount
    uniquely identifies the order)."""
    taken = {int(r["expected_cents"]) for r in conn.execute(
        "SELECT expected_cents FROM orders WHERE method=? AND status IN ('pending','paid') "
        "AND (reservation_expires_at IS NULL OR reservation_expires_at >= datetime('now', ?))",
        (method, "-%d minutes" % config.AMOUNT_COOLDOWN_MINUTES)).fetchall()}
    c = int(base_cents)
    while c in taken:
        c += 1
    return c


def create_order(method: str, quote_amount: float, *, merchant_order_id=None,
                 notify_url=None, redirect_url=None, metadata=None,
                 pay_address=None, pay_memo=None, address_index=None,
                 amount_match=False, ttl_minutes=None) -> dict:
    """Reserve an order. In per-order-address mode `pay_address`/`address_index` are
    supplied by the caller (derived from the xpub). In amount-match mode we nudge the
    cents to a unique value on the shared address. Returns the stored order dict."""
    import secrets
    ttl = int(ttl_minutes or config.RESERVATION_TTL_MINUTES)
    base_cents = int(round(float(quote_amount) * 100))
    if base_cents <= 0:
        raise ValueError("amount must be > 0")
    trade_id = secrets.token_urlsafe(18)
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if merchant_order_id:
            dup = conn.execute("SELECT * FROM orders WHERE merchant_order_id=?",
                               (merchant_order_id,)).fetchone()
            if dup:
                conn.rollback()
                return dict(dup)
        expected_cents = _unique_amount_cents(conn, method, base_cents) if amount_match else base_cents
        cur = conn.execute(
            "INSERT INTO orders(trade_id, merchant_order_id, method, quote_amount, quote_currency,"
            " expected_cents, address_index, pay_address, pay_memo, notify_url, redirect_url,"
            " metadata, reservation_expires_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?, datetime('now', ?))",
            (trade_id, merchant_order_id, method, float(quote_amount), config.QUOTE_CURRENCY,
             expected_cents, address_index, pay_address, pay_memo, notify_url, redirect_url,
             json.dumps(metadata) if metadata else None, "+%d minutes" % ttl))
        oid = cur.lastrowid
        conn.commit()
        return dict(conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
    finally:
        conn.close()


# --- crediting (idempotent) -------------------------------------------------
def _claim_reference(conn: sqlite3.Connection, reference: str, reference_type: str,
                     order_id: int) -> bool:
    """Burn a reference into the registry. Returns False if already used (replay)."""
    norm = normalize_reference(reference)
    if not norm:
        return True  # nothing to claim (e.g. synthetic) — allow
    try:
        conn.execute(
            "INSERT INTO payment_reference_registry(normalized_reference, original_reference,"
            " reference_type, order_id) VALUES(?,?,?,?)",
            (norm, str(reference), reference_type, order_id))
        return True
    except sqlite3.IntegrityError:
        return False


def _apply_credit(conn: sqlite3.Connection, order: sqlite3.Row, cents: int, txid: str,
                  reference_type: str) -> dict:
    """Core credit: burn txid, accumulate, flip to PAID when covered. Assumes an open
    IMMEDIATE transaction. Returns a status dict."""
    oid = int(order["id"])
    if txid and not _claim_reference(conn, txid, reference_type, oid):
        return {"status": "already_used", "order_id": oid}
    new_total = int(order["received_cents"] or 0) + int(cents)
    tx_hashes = (order["tx_hashes"] or "")
    if txid and txid not in tx_hashes:
        tx_hashes = (tx_hashes + "," + txid).strip(",")
    conn.execute("UPDATE orders SET received_cents=?, tx_hashes=? WHERE id=?",
                 (new_total, tx_hashes, oid))
    expected = int(order["expected_cents"] or 0)
    # still creditable? pending and (not expired OR inside the late cooldown)
    active = str(order["status"]) == STATUS_PENDING
    covered = new_total >= expected
    base = {"order_id": oid, "trade_id": order["trade_id"], "credited_cents": int(cents),
            "received_cents": new_total, "expected_cents": expected,
            "overpaid_cents": max(0, new_total - expected)}
    if active and covered:
        conn.execute("UPDATE orders SET status=?, paid_at=datetime('now') WHERE id=? AND status=?",
                     (STATUS_PAID, oid, STATUS_PENDING))
        return {**base, "status": "paid"}
    return {**base, "status": "partial" if active else "topup"}


def credit_by_address(reference_type: str, to_address: str, cents: int, txid: str) -> dict:
    """Per-order-address mode: credit an on-chain transfer to whichever order owns
    `to_address`. Matched by ADDRESS ALONE — EVM per-order addresses are globally
    unique, so this credits the right order even if the buyer used the WRONG EVM
    network (that's how wrong-network recovery re-uses this path). Idempotent by txid;
    `reference_type` (the chain it arrived on) only tags the registry entry."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute(
            "SELECT * FROM orders WHERE lower(pay_address)=lower(?) ORDER BY id DESC LIMIT 1",
            (to_address,)).fetchone()
        if not order:
            conn.rollback()
            return {"status": "no_order", "address": to_address}
        res = _apply_credit(conn, order, cents, txid, reference_type)
        conn.commit()
        return res
    finally:
        conn.close()


def all_evm_order_addresses() -> list[dict]:
    """Every distinct per-order EVM address (any status) — for wrong-network recovery,
    which must check addresses whose on-chain payment never credited on the intended
    chain (so they look 'unpaid')."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT MAX(address_index) AS address_index, pay_address FROM orders "
            "WHERE address_index IS NOT NULL AND pay_address IS NOT NULL "
            "GROUP BY lower(pay_address) ORDER BY address_index DESC").fetchall()
        return [{"address_index": r["address_index"], "pay_address": r["pay_address"]} for r in rows]
    finally:
        conn.close()


def credit_by_amount(method: str, cents: int, txid: str) -> dict:
    """Amount-match mode: credit a WHOLE-cent transfer to the order that reserved this
    exact cents value on the shared address. Idempotent by txid."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute(
            "SELECT * FROM orders WHERE method=? AND expected_cents=? AND status=? "
            "ORDER BY id DESC LIMIT 1", (method, int(cents), STATUS_PENDING)).fetchone()
        if not order:
            conn.rollback()
            return {"status": "no_order", "cents": int(cents)}
        res = _apply_credit(conn, order, cents, txid, method)
        conn.commit()
        return res
    finally:
        conn.close()


def credit_by_memo(method: str, memo: str, cents: int, txid: str) -> dict:
    """TON memo mode: credit to the order whose pay_memo matches the transfer comment."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute(
            "SELECT * FROM orders WHERE method=? AND pay_memo=? ORDER BY id DESC LIMIT 1",
            (method, memo)).fetchone()
        if not order:
            conn.rollback()
            return {"status": "no_order", "memo": memo}
        res = _apply_credit(conn, order, cents, txid, method)
        conn.commit()
        return res
    finally:
        conn.close()


# --- queries / lifecycle ----------------------------------------------------
def get_order(trade_id: str = None, order_id: int = None) -> dict | None:
    conn = connect()
    try:
        if order_id is not None:
            r = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        else:
            r = conn.execute("SELECT * FROM orders WHERE trade_id=?", (trade_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def list_orders(status=None, limit=200) -> list[dict]:
    conn = connect()
    try:
        if status:
            rows = conn.execute("SELECT * FROM orders WHERE status=? ORDER BY id DESC LIMIT ?",
                                (status, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def active_order_addresses(method: str) -> list[dict]:
    """Per-order addresses to watch (pending or recently paid, within cooldown)."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, address_index, pay_address, expected_cents, received_cents "
            "FROM orders WHERE method=? AND pay_address IS NOT NULL "
            "AND (status='pending' OR reservation_expires_at >= datetime('now', ?))",
            (method, "-%d minutes" % config.AMOUNT_COOLDOWN_MINUTES)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def expire_orders() -> int:
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE orders SET status=? WHERE status=? AND reservation_expires_at < datetime('now')",
            (STATUS_EXPIRED, STATUS_PENDING))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# --- webhook queue ----------------------------------------------------------
def enqueue_webhook(order_id: int, url: str, payload: dict) -> None:
    conn = connect()
    try:
        conn.execute("INSERT INTO webhook_queue(order_id, url, payload, next_attempt_at) VALUES(?,?,?,?)",
                     (order_id, url, json.dumps(payload), time.time()))
        conn.commit()
    finally:
        conn.close()


def due_webhooks(limit=20) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM webhook_queue WHERE status='pending' AND next_attempt_at<=? "
            "ORDER BY id ASC LIMIT ?", (time.time(), limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_webhook(wid: int, *, delivered=False, error=None, retry_delay=None,
                 max_retries=6) -> None:
    conn = connect()
    try:
        if delivered:
            conn.execute("UPDATE webhook_queue SET status='delivered', attempts=attempts+1 WHERE id=?", (wid,))
        else:
            row = conn.execute("SELECT attempts FROM webhook_queue WHERE id=?", (wid,)).fetchone()
            attempts = (int(row["attempts"]) if row else 0) + 1
            if attempts >= max_retries:
                conn.execute("UPDATE webhook_queue SET status='failed', attempts=?, last_error=? WHERE id=?",
                             (attempts, str(error)[:300], wid))
            else:
                conn.execute("UPDATE webhook_queue SET attempts=?, last_error=?, next_attempt_at=? WHERE id=?",
                             (attempts, str(error)[:300], time.time() + (retry_delay or 60), wid))
        conn.commit()
    finally:
        conn.close()
