# The accumulating BEP20 address pool

Per-order addresses are the private, easy-to-reconcile default: every order gets its own
address and payments are matched **by address**, never by amount (so there is no
front-running surface — see [`SECURITY.md`](SECURITY.md) §3.3). The one cost is
**sweeping**: to collect many order addresses into one cold wallet you pay EVM gas *per
address*. For a shop full of small orders that gas can rival the order itself.

The **address pool** removes that cost without giving up the per-order-address model. It is
**off by default** — with it off, the allocator is the original never-reuse monotonic index
and behaviour is byte-for-byte unchanged. This doc explains the problem, the mechanism, the
rotation and attribution rules that keep it money-safe, the settings, and the single honest
residual risk.

Everything here is EVM-generic but ships tuned for **BEP20 (BSC)**, where fees are lowest
and small-order volume is highest. Litecoin solves the same "small order" problem a
different way — near-zero sweep fees per address ([`LITECOIN.md`](LITECOIN.md)); the pool is
the EVM answer.

---

## 1. The problem — fixed sweep gas makes tiny orders uneconomical

Each per-order address holds one order's payment. Sweeping it to cold storage is a token
transfer that costs a **fixed** amount of gas regardless of how much it moves. Ten $1
orders arrive at ten addresses → **ten** sweeps → ten gas fees. On a busy chain that can
eat a meaningful slice of a small order, and it scales with order *count*, not order
*value* — exactly backwards for a low-ticket catalogue.

## 2. The solution — reuse a bounded set of addresses, sweep once at a threshold

Instead of a brand-new address forever, hand out addresses from a **bounded pool** of size
`N` (indices `1..N`). Because addresses are reused, several buyers' small payments
**accumulate on the same address on-chain over time**, and you sweep that address **once**
when its balance crosses a dollar threshold — amortising a single gas fee across many
orders. The pool **self-sizes**: if every address is busy at once, the allocator mints a
new index rather than blocking a buyer, so `N` is a floor on concurrency, not a hard cap.

Reuse is only safe if two buyers can never be *simultaneously* live on one address and a
late payment can never land on the wrong occupant. That is what the rotation and
attribution rules below guarantee.

## 3. Rotation rules — when an address may be re-handed out

The pool allocator (`_next_..._pool_index`) picks, within `1..N`, the **least-recently-used
reissuable** index. An index is **reissuable** only when **both** hold:

1. **No open order.** It has **no `pending` order that is still within its reservation
   window** (`reservation_expires_at`). An address with any live, unexpired order is
   **LOCKED** and will not be handed to anyone else.
2. **Cooldown fully elapsed.** Its most recent activity (`last_partial_at`, else
   `created_at`) is **older than the reissue cooldown**, so no valid payment for a prior
   occupant can still arrive.

Selection among reissuable indices is **LRU** (oldest last-activity first), which spreads
wear and maximises the accumulation window. **If nothing is reissuable, the allocator mints
the next new index** (grows the pool) — a buyer is never blocked; the reuse benefit simply
resumes as addresses free up.

### The cooldown must cover the late-payment window

The reissue cooldown (`pool_reuse_cooldown_minutes`) is clamped to be **≥ the amount/late
window** (`AMOUNT_COOLDOWN_MINUTES`, the 24h window a slow buyer can still pay in) and
defaults to **48h** for extra margin. This is the load-bearing invariant: an address is
released to a new buyer **only after the previous occupant's entire late-payment window has
closed**, so "current buyer" and "in-window prior buyer" can never overlap on one address.

### Pending-scoped unique index — the concurrency backstop

A partial UNIQUE index enforces **at most one open order per address** at the database
level, independent of the allocator:

```sql
CREATE UNIQUE INDEX idx_deposits_gateway_open_address
  ON deposits(method, address_index)
  WHERE status='pending' AND address_index IS NOT NULL;
```

Under a race (two allocations landing on the same index at once), the second `INSERT`
fails on this index rather than creating two live orders on one address. The 24h/48h
cooldown — which a partial index can't express (no `now()` in a SQLite partial predicate) —
is enforced by the allocator; this index enforces the *instantaneous* one-open-order rule.
With reuse OFF (one row per index ever), the index is trivially satisfied and nothing
changes.

## 4. Attribution safety — a payment always credits exactly one buyer

When a transfer arrives at a pooled address, the credit path resolves the occupant with a
**two-tier resolver**, then burns the txid before crediting (idempotent, per
[`SECURITY.md`](SECURITY.md) §2):

- **Tier 1 — the OPEN occupant.** The single `pending` order on that address whose
  reservation has **not** expired (newest such row). This is the buyer currently checked
  out; their in-flight payment credits them.
- **Tier 2 — the most-recent prior occupant.** If there is no open order, the newest
  historical row for the address. This catches a **late** payment arriving after the order
  expired but **before** the address was reissued.

Because the reissue cooldown (§3) keeps Tier 1 and Tier 2 **mutually exclusive** — an
address can't have a fresh open order *and* still be inside a prior occupant's late window —
a txid resolves to **exactly one** buyer. With reuse OFF this collapses to the original
"newest row for the address" rule, byte-for-byte.

## 5. Settings

| Setting (DB key / env) | Default | Meaning |
|---|---|---|
| `pool_enabled` / `OPG_POOL_ENABLED` | `false` | Master switch. Off → original never-reuse monotonic allocator (unchanged behaviour). |
| `pool_size` / `OPG_POOL_SIZE` | `30` | `N`, the pool's index range `1..N` and its concurrency floor. The pool grows past `N` on demand and shrinks back to reuse as addresses free. |
| `pool_reuse_cooldown_minutes` / `OPG_POOL_REUSE_COOLDOWN_MINUTES` | `2880` (48h) | How long after last activity before an index is reissuable. **Clamped to ≥ `AMOUNT_COOLDOWN_MINUTES`** (24h) — the invariant that makes reuse safe. |
| `sweep_min_usd_<method>` / per-chain sweep $ threshold | per deployment | Don't sweep an address until its accumulated balance crosses this dollar value — this is what turns many small deposits into one gas-efficient sweep. |

Turn it on for a low-fee, high-small-order chain (BEP20) and leave it off for chains where
per-order privacy matters more than gas, or where you already use Litecoin for small orders.

## 6. The one residual risk (honest)

There is exactly **one** edge case the design does not fully eliminate:

> A payment that is **both** (a) more than the full late-payment window late **and** (b)
> arrives **after** the address has already been reissued to a new buyer will credit the
> **current** occupant, not the original late payer.

Why it's near-impossible in practice: it requires a buyer to pay **> 24–48 hours** after
their order was created (past the entire reservation *and* the reissue cooldown) **and** for
that address to have already been re-handed out. For any prompt payer this window never
opens — the cooldown is specifically sized so the address is still locked/cooling during the
whole time a real payment could land.

How to eliminate it entirely if your risk tolerance requires zero residual:

- **Unique-amount tail per order** — add the amount-match discriminator on top of the
  address so even a collision resolves by exact cents (reintroduces a small front-running
  consideration; see [`SECURITY.md`](SECURITY.md) §3.3), or
- **Per-buyer permanent addresses** — never reuse across buyers (each buyer keeps one
  address), which removes reuse — and its gas savings — for the affected buyers only.

For most shops the default (bounded pool + 48h cooldown) is the right trade: near-total gas
savings on small orders, with a residual that only a payer who waited **days** could ever
trigger.
