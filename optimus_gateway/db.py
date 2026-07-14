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
    swept_at TEXT,                      -- stamped when the address was swept to cold
    sweep_txid TEXT,                    -- the forward txid that swept this address
    last_activity_at TEXT,              -- last credit to this order's address (pool LRU)
    reservation_expires_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    paid_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_method_status ON orders(method, status);
CREATE INDEX IF NOT EXISTS idx_orders_amount ON orders(method, expected_cents, status);
CREATE INDEX IF NOT EXISTS idx_orders_address ON orders(pay_address);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_merchant ON orders(merchant_order_id) WHERE merchant_order_id IS NOT NULL;
-- Accumulating-pool backstop: AT MOST ONE OPEN (pending) order may hold a per-order
-- address at a time. Scoped to addressed orders (address_index NOT NULL) so it never
-- constrains the shared address used by amount-match / TON mode (many pending there).
-- Replaces the old unconditional UNIQUE(address_index): a reissued index is reused only
-- AFTER its prior order closes, so the two never collide, and a concurrent double-alloc
-- raises IntegrityError -> the allocator retries a different index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_open_address ON orders(address_index)
    WHERE status='pending' AND address_index IS NOT NULL;

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

CREATE TABLE IF NOT EXISTS sweep_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    method TEXT,                        -- the chain the forward went out on
    txid TEXT,                          -- forward tx hash (per-order address -> main/cold)
    amount_cents INTEGER,               -- USD value forwarded, in cents
    from_address TEXT,                  -- the per-order (hot) address swept
    to_address TEXT,                    -- the cold / main destination
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sweep_log_txid ON sweep_log(method, txid);
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
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent migrations for DBs created before a column existed. New
    tables/indexes come from `_SCHEMA` (all IF NOT EXISTS), but SQLite has no
    ADD COLUMN IF NOT EXISTS, so columns are added guarded by a table-info probe."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
    for col, decl in (("sweep_txid", "TEXT"),
                      ("last_activity_at", "TEXT")):
        if col not in have:
            conn.execute("ALTER TABLE orders ADD COLUMN %s %s" % (col, decl))


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
_TRUE = {"1", "true", "yes", "on", "y"}
_EVM_COUNTER = "_evm"  # single GLOBAL EVM index space (keeps every EVM address unique)


def next_address_index(conn: sqlite3.Connection, method: str) -> int:
    conn.execute("INSERT OR IGNORE INTO address_counter(method,next_index) VALUES(?,1)", (method,))
    row = conn.execute("SELECT next_index FROM address_counter WHERE method=?", (method,)).fetchone()
    idx = int(row["next_index"])
    conn.execute("UPDATE address_counter SET next_index=? WHERE method=?", (idx + 1, method))
    return idx


# --- Accumulating address pool (gas-saving reuse) ---------------------------
# OFF by default. When off, per-order addresses are minted monotonically (never reused),
# which is byte-for-byte the original behavior. When on, a small pool of addresses is
# reissued LRU-style once every prior order on an address has fully closed AND cooled
# down past the late-payment window — so small payments pile up on-chain and one sweep
# collects several orders' funds, saving gas. The two-tier attribution in
# credit_by_address is what keeps reuse money-safe (see that function).
def pool_enabled() -> bool:
    return get_setting("pool_enabled", "false").strip().lower() in _TRUE


def pool_size() -> int:
    try:
        return max(1, int(get_setting("pool_size", "30") or 30))
    except (TypeError, ValueError):
        return 30


def pool_reuse_cooldown_minutes() -> int:
    """How long an UNPAID/PARTIAL/EXPIRED address stays parked before it is re-handed to a
    new order. (A fully-PAID address reuses immediately — its funds are already confirmed.)
    Measured from creation/last-partial; the pay window is RESERVATION_TTL_MINUTES, so the
    default 60 frees an abandoned address ~20 min AFTER it expires — enough for a last-second
    payment to confirm and credit the ORIGINAL order first. Floored at
    config.POOL_REISSUE_FLOOR_MINUTES (pay window + a confirm tail) so it can't be set so low
    that a normal late confirm lands on the next occupant. Note: the watcher still credits any
    address for config.AMOUNT_COOLDOWN_MINUTES (see active_order_addresses), so shortening this
    only speeds recycling — it does NOT stop late payments being credited. Trade-off: shorter =
    faster sweeps, but a larger (rare) residual that a >window-late payment credits the current
    occupant instead of the original payer."""
    try:
        v = int(get_setting("pool_reuse_cooldown_minutes", "60") or 60)
    except (TypeError, ValueError):
        v = 60
    return max(int(config.POOL_REISSUE_FLOOR_MINUTES), v)


def next_evm_address_index(conn: sqlite3.Connection) -> int:
    """Allocate a per-order EVM HD index (single global space). Pool OFF -> the next
    never-reused monotonic index. Pool ON -> the FULLEST REISSUABLE index in 1..N.

    REISSUABLE = no active OPEN order AND the address cannot still receive a legit payment
    for a prior occupant: a PAID (fully-covered) order expects nothing more, so a paid
    address is reusable IMMEDIATELY (funds already confirmed on it); an unpaid/partial/expired
    order could still get a late payment, so its address is held until past the reuse cooldown
    (>= the late-payment window). Among reissuable indices, pick the one holding the MOST
    un-swept balance (closest to the sweep threshold) so it sweeps SOONEST, tie-broken by LRU.
    If none is free, MINT a new index (grows the pool; a caller is never blocked). Open a txn first."""
    if not pool_enabled():
        return next_address_index(conn, _EVM_COUNTER)
    n = pool_size()
    cooldown = pool_reuse_cooldown_minutes()
    row = conn.execute(
        """
        SELECT address_index
        FROM orders
        WHERE address_index IS NOT NULL AND address_index BETWEEN 1 AND ?
        GROUP BY address_index
        HAVING SUM(CASE
                     -- an active OPEN order always locks the address
                     WHEN status='pending'
                          AND (reservation_expires_at IS NULL OR reservation_expires_at > datetime('now'))
                       THEN 1
                     -- an order that is NOT fully paid could still get a legit late payment within
                     -- the cooldown window -> hold the address until then. A PAID order expects
                     -- nothing more -> never blocks, so a paid address is reusable IMMEDIATELY
                     -- (the funds are already confirmed on-chain).
                     WHEN status <> 'paid'
                          AND datetime(COALESCE(last_activity_at, created_at), '+' || ? || ' minutes') >= datetime('now')
                       THEN 1
                     ELSE 0
                   END) = 0
        ORDER BY SUM(CASE WHEN swept_at IS NULL THEN COALESCE(received_cents,0) ELSE 0 END) DESC,
                 MAX(COALESCE(last_activity_at, created_at)) ASC
        LIMIT 1
        """,
        (n, cooldown)).fetchone()
    if row and row["address_index"] is not None:
        return int(row["address_index"])
    # Nothing reissuable -> mint the next new index (grows the pool; never blocks).
    return next_address_index(conn, _EVM_COUNTER)


def create_addressed_order(method: str, quote_amount: float, derive_address,
                           *, merchant_order_id=None, notify_url=None, redirect_url=None,
                           metadata=None, ttl_minutes=None, max_index_tries=25) -> dict:
    """Reserve a PER-ORDER EVM address order with the reusable pool. `derive_address(index)
    -> str` turns an HD index into its address; the caller supplies it so this layer stays
    crypto-free (gateway.py passes the xpub derivation). Allocates a pool index (reused or
    fresh), flips any stale pending-but-EXPIRED row occupying a reissued index to 'expired'
    so the at-most-one-open-order backstop can't falsely block it, then inserts — retrying a
    DIFFERENT index if the partial-unique backstop trips under concurrency. expected_cents is
    the EXACT amount (attribution is by address; any amount accumulates until covered).
    Returns the stored order dict (or an existing one on merchant_order_id idempotency)."""
    import secrets
    ttl = int(ttl_minutes or config.RESERVATION_TTL_MINUTES)
    base_cents = int(round(float(quote_amount) * 100))
    if base_cents <= 0:
        raise ValueError("amount must be > 0")
    conn = connect()
    try:
        if merchant_order_id:
            dup = conn.execute("SELECT * FROM orders WHERE merchant_order_id=?",
                               (merchant_order_id,)).fetchone()
            if dup:
                return dict(dup)
        for _ in range(max(1, int(max_index_tries))):
            trade_id = secrets.token_urlsafe(18)
            try:
                conn.execute("BEGIN IMMEDIATE")
                # re-check the merchant idempotency key under the lock (concurrent create)
                if merchant_order_id:
                    dup = conn.execute("SELECT * FROM orders WHERE merchant_order_id=?",
                                       (merchant_order_id,)).fetchone()
                    if dup:
                        conn.rollback()
                        return dict(dup)
                idx = next_evm_address_index(conn)
                addr = derive_address(idx)
                if not addr:
                    conn.rollback()
                    raise RuntimeError("address derivation returned empty")
                # Reuse safety: a reissued index may still carry a pending-but-EXPIRED row
                # (past TTL, not yet swept up by expire_orders). Flip it so the partial-
                # unique-on-pending backstop doesn't falsely block this new order.
                conn.execute(
                    "UPDATE orders SET status=? WHERE address_index=? AND status=? "
                    "AND reservation_expires_at IS NOT NULL "
                    "AND reservation_expires_at <= datetime('now')",
                    (STATUS_EXPIRED, idx, STATUS_PENDING))
                cur = conn.execute(
                    "INSERT INTO orders(trade_id, merchant_order_id, method, quote_amount,"
                    " quote_currency, expected_cents, address_index, pay_address, notify_url,"
                    " redirect_url, metadata, reservation_expires_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?, datetime('now', ?))",
                    (trade_id, merchant_order_id, method, float(quote_amount), config.QUOTE_CURRENCY,
                     base_cents, idx, addr, notify_url, redirect_url,
                     json.dumps(metadata) if metadata else None, "+%d minutes" % ttl))
                oid = cur.lastrowid
                conn.commit()
                return dict(conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
            except sqlite3.IntegrityError:
                # the open-address backstop tripped (another worker took this index) —
                # roll back and try a different index
                conn.rollback()
                continue
        raise RuntimeError("could not allocate a free gateway address (pool exhausted?)")
    finally:
        conn.close()


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
                     order_id: int, ref_suffix: str = None) -> bool:
    """Burn a reference into the registry. Returns False if already used (replay).

    `ref_suffix` disambiguates references that share a normalized txid: a single
    transaction can emit MULTIPLE watched Transfer events (batch/multisend), so the
    registry key is normalized_txid#logIndex — otherwise only the first transfer in
    such a tx would credit and the rest would be dropped as 'already_used'. Wrong-net
    recovery reuses this with an arrived-total suffix so repeat deposits stay unique."""
    base = normalize_reference(reference)
    if not base:
        return True  # nothing to claim (e.g. purely synthetic) — allow
    norm = base if ref_suffix in (None, "") else "%s#%s" % (base, ref_suffix)
    # Backward-compat across the upgrade that introduced the #suffix key format: a
    # pre-upgrade deployment stored bare `base` (no suffix). If that legacy key was
    # already burned for this txid, treat the suffixed key as used too, so the first
    # post-upgrade re-scan of the RESCAN_OVERLAP window doesn't re-credit it.
    if norm != base and conn.execute(
            "SELECT 1 FROM payment_reference_registry WHERE normalized_reference=?",
            (base,)).fetchone():
        return False
    try:
        conn.execute(
            "INSERT INTO payment_reference_registry(normalized_reference, original_reference,"
            " reference_type, order_id) VALUES(?,?,?,?)",
            (norm, str(reference), reference_type, order_id))
        return True
    except sqlite3.IntegrityError:
        return False


def _apply_credit(conn: sqlite3.Connection, order: sqlite3.Row, cents: int, txid: str,
                  reference_type: str, ref_suffix: str = None) -> dict:
    """Core credit: burn txid, accumulate, flip to PAID when covered. Assumes an open
    IMMEDIATE transaction. Returns a status dict."""
    oid = int(order["id"])
    if txid and not _claim_reference(conn, txid, reference_type, oid, ref_suffix):
        return {"status": "already_used", "order_id": oid}
    new_total = int(order["received_cents"] or 0) + int(cents)
    tx_hashes = (order["tx_hashes"] or "")
    if txid and txid not in tx_hashes:
        tx_hashes = (tx_hashes + "," + txid).strip(",")
    # stamp last_activity_at so the pool LRU allocator can tell when this address last
    # saw money (a reissued address must be idle past the reuse cooldown).
    conn.execute("UPDATE orders SET received_cents=?, tx_hashes=?, last_activity_at=datetime('now') WHERE id=?",
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


def credit_by_address(reference_type: str, to_address: str, cents: int, txid: str,
                      ref_suffix: str = None) -> dict:
    """Per-order-address mode: credit an on-chain transfer to whichever order owns
    `to_address`. Matched by ADDRESS ALONE — EVM per-order addresses are globally
    unique, so this credits the right order even if the buyer used the WRONG EVM
    network (that's how wrong-network recovery re-uses this path). Idempotent by
    (txid, ref_suffix); `reference_type` (the chain it arrived on) only tags the entry."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # TWO-TIER attribution — the money-safety change that makes the accumulating pool
        # (address reuse) correct. A payment credits the SINGLE currently-OPEN order on the
        # address (Tier 1: addressed, pending, not past its reservation), else the newest
        # prior occupant (Tier 2 = the original "newest row" rule — a late payment / top-up).
        # Pool reuse is gated so an address is only re-handed to a new order AFTER the prior
        # one's full late-payment window elapses, which makes Tier 1 (current) and Tier 2
        # (in-window prior) mutually exclusive -> a txid resolves to exactly one order. With
        # reuse OFF (one row per address) this is byte-for-byte the old behavior.
        order = conn.execute(
            "SELECT * FROM orders WHERE lower(pay_address)=lower(?) AND address_index IS NOT NULL "
            "AND status='pending' AND (reservation_expires_at IS NULL OR reservation_expires_at > datetime('now')) "
            "ORDER BY id DESC LIMIT 1",
            (to_address,)).fetchone()
        if not order:
            order = conn.execute(
                "SELECT * FROM orders WHERE lower(pay_address)=lower(?) AND address_index IS NOT NULL "
                "ORDER BY id DESC LIMIT 1",
                (to_address,)).fetchone()
        if not order:
            conn.rollback()
            return {"status": "no_order", "address": to_address}
        res = _apply_credit(conn, order, cents, txid, reference_type, ref_suffix)
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


def sweepable_order_addresses(method: str = None) -> list[dict]:
    """Per-order ADDRESSES that have received funds and are not yet swept, DE-DUPLICATED to
    ONE row per address (GROUP BY lower(pay_address)). With the accumulating pool an address
    holds several orders' coins and the sweep is per-address (one on-chain balance), so the
    sweeper must process each address once, not once per order row. With reuse off this is
    one-row-per-address anyway, so the grouping is a no-op. Pass `method` to scope to a
    single chain, or leave None for every EVM per-order address."""
    conn = connect()
    try:
        if method:
            rows = conn.execute(
                "SELECT MIN(id) AS id, pay_address, MAX(address_index) AS address_index, "
                "SUM(COALESCE(received_cents,0)) AS received_cents, "
                "SUM(COALESCE(expected_cents,0)) AS expected_cents "
                "FROM orders WHERE method=? AND address_index IS NOT NULL "
                "AND COALESCE(received_cents,0) > 0 AND swept_at IS NULL "
                "GROUP BY lower(pay_address) ORDER BY MIN(id) ASC", (method,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT MIN(id) AS id, pay_address, MAX(address_index) AS address_index, "
                "SUM(COALESCE(received_cents,0)) AS received_cents, "
                "SUM(COALESCE(expected_cents,0)) AS expected_cents "
                "FROM orders WHERE address_index IS NOT NULL "
                "AND COALESCE(received_cents,0) > 0 AND swept_at IS NULL "
                "GROUP BY lower(pay_address) ORDER BY MIN(id) ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_address_swept(pay_address: str, txid: str = "") -> None:
    """Mark EVERY funded, unswept order row on a per-order address swept after an
    address-level sweep (the pool accumulates several orders' coins on one address; one
    sweep clears them all). Idempotent — rows already stamped are untouched."""
    addr = (pay_address or "").strip()
    if not addr:
        return
    conn = connect()
    try:
        conn.execute(
            "UPDATE orders SET swept_at=datetime('now'), sweep_txid=? "
            "WHERE lower(pay_address)=lower(?) AND swept_at IS NULL AND COALESCE(received_cents,0) > 0",
            (str(txid or ""), addr))
        conn.commit()
    finally:
        conn.close()


def log_sweep(method: str, txid: str, amount_cents: int, from_address: str,
              to_address: str) -> bool:
    """Idempotently record an outbound sweep (per-order address -> main/cold) so EVERY
    forward is visible. Idempotent by (method, txid): a duplicate is a no-op. Never raises
    — sweep logging must not break a sweep. Returns True if recorded for the first time."""
    if not txid or not method:
        return False
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO sweep_log(method, txid, amount_cents, from_address, to_address) "
            "VALUES(?,?,?,?,?)",
            (str(method), str(txid), int(amount_cents or 0), str(from_address or ""), str(to_address or "")))
        conn.commit()
        return (cur.rowcount or 0) > 0
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def list_sweeps(limit: int = 200) -> list[dict]:
    """Recent sweep-forward log rows, newest first (admin / reconciliation view)."""
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM sweep_log ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def all_active_order_addresses() -> list[dict]:
    """Every per-order address for a pending or recently-active order ACROSS ALL EVM
    methods — so each chain's watcher can also detect a WRONG-NETWORK payment to one of
    our addresses. credit_by_address matches by address alone and is idempotent by
    (txid, logIndex), so the watcher crediting a foreign-chain deposit here can never
    double-credit the correct-chain path. This unifies crediting in ONE place (the
    watcher); the sweeper only moves funds."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT pay_address FROM orders WHERE pay_address IS NOT NULL "
            "AND address_index IS NOT NULL "
            "AND (status='pending' OR reservation_expires_at >= datetime('now', ?))",
            ("-%d minutes" % config.AMOUNT_COOLDOWN_MINUTES,)).fetchall()
        return [{"pay_address": r["pay_address"]} for r in rows]
    finally:
        conn.close()


def credit_by_amount(method: str, cents: int, txid: str, ref_suffix: str = None) -> dict:
    """Amount-match mode: credit a WHOLE-cent transfer to the order that reserved this
    exact cents value on the shared address. Idempotent by (txid, ref_suffix)."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute(
            "SELECT * FROM orders WHERE method=? AND expected_cents=? AND status=? "
            "ORDER BY id DESC LIMIT 1", (method, int(cents), STATUS_PENDING)).fetchone()
        if not order:
            conn.rollback()
            return {"status": "no_order", "cents": int(cents)}
        res = _apply_credit(conn, order, cents, txid, method, ref_suffix)
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
