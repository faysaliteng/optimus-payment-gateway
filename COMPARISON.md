# How Optimus compares — an honest look

> **TL;DR — the defensible claim:** Optimus is the **most advanced and money-safe
> *self-hosted, non-custodial* USDT/USDC payment gateway.** Your keys, your addresses,
> zero fees, no KYC. It is a *different category* from custodial processors like
> Cryptomus — and a clear generation ahead of the open-source alternative, epusdt.

This document is deliberately honest. It tells you where Optimus genuinely wins, and
where another tool is the better fit. If you need custodial fiat settlement, 100+ coins,
or compliance-as-a-service, a hosted processor is the right tool — and we say so below.

---

## The category Optimus wins in

**Self-hosted, non-custodial, zero-fee, multi-chain USDT/USDC acceptance with true
per-order HD (xpub) address attribution.** In that lane the combination is genuinely
strong:

- **7 EVM chains + TON from a single wallet** (BSC, Ethereum, Polygon, Arbitrum,
  Optimism, Base, Avalanche + TON), USDT + USDC + bridged variants.
- **Watch-only by default** — the server holds *zero* private keys in the recommended
  mode; funds land at addresses only your offline seed controls.
- A **tested, integer-cents ledger** with idempotent double-credit protection.
- **Auto-sweep + gas tank** and an operationally distinctive **wrong-network recovery**
  path (reclaim funds a buyer paid on the wrong EVM chain).

It is **not** trying to be a custodial fiat processor: no card on-ramp, no fiat
settlement, no mass payouts, no auto-conversion, no KYC/AML-as-a-service, no mobile app,
no 100-coin breadth. Choosing Optimus is a deliberate trade of *breadth-and-hosted-
convenience* for *self-custody, zero fees, and no counterparty freeze risk*.

---

## Head-to-head

| Dimension | **Optimus (this repo)** | epusdt | Cryptomus |
|---|---|---|---|
| **Custody** | Non-custodial; watch-only mode holds **zero** keys | Non-custodial, watch-only | **Custodial** — holds funds, can freeze |
| **Chains / tokens** | 7 EVM + TON · USDT + USDC + bridged | TRC20-USDT only (proven core) | 100+ coins + fiat |
| **Per-order address** | **Unique HD xpub address per order** | Shared reused pool + amount match | Per-invoice (platform-side) |
| **Fees** | **0%** | 0% | 2% default (→0.4% negotiated) |
| **KYC / signup** | None | None | **Mandatory KYC** (since Feb 2025) |
| **Self-hostable** | Yes — 1 process, SQLite/WAL (no MySQL/Redis) | Yes — Go binary (+MySQL/Redis classic) | **No** (closed SaaS) |
| **Auto-sweep + gas tank** | **Yes**, just-in-time gas | No | Platform-managed |
| **Wrong-network recovery** | **Yes** — reclaim + credit wrong-chain deposits | No | Not a merchant feature |
| **Idempotency / no double-credit** | **Tested** `(txid, logIndex)` registry inside the credit txn | Amount match; no published replay proof | Not merchant-inspectable |
| **Webhook security** | **HMAC-SHA256** | MD5 | MD5 |
| **API auth default** | **Safe-by-default** (localhost-only until a key is set) | Per-merchant key (MD5 sig) | Per-merchant key (MD5 sig) |
| **SSRF guard on notify_url** | **Yes** (private/loopback blocked, no redirect-follow) | Not documented | N/A (hosted) |
| **Detection integrity** | Crash-safe cursor + per-log re-verification vs a lying RPC | Single explorer (e.g. TronGrid) SPOF | Runs at scale (trust the custodian) |
| **Hosted checkout polish** | Basic (QR + poll) | Basic | **Polished** (invoicing, recurring, links) |
| **Source** | Open (MIT), self-hostable, CI + tests | Open (GPLv3), large fork ecosystem | Closed |

Winners, honestly: **Optimus** leads every self-hosted / non-custodial / security row.
**Cryptomus** leads on raw breadth (coins, fiat, hosted feature polish). **epusdt** is the
simplest if you specifically need Tron/TRC-20 and nothing else.

---

## What Optimus does that epusdt cannot

Concrete deltas over epusdt's proven TRC20-USDT core:

- **True multi-chain from one wallet** — 7 EVM chains + TON vs TRC20-USDT only.
- **Per-order xpub / HD addresses** — a unique derived address per order, vs a shared
  reused pool disambiguated only by amount (front-runnable at volume).
- **First-class USDC** (+ bridged USDC.e / USDbC / USDT.e) on every EVM chain.
- **Auto-sweep to cold storage + per-chain gas tank** with just-in-time gas top-up —
  epusdt has no sweep/consolidation.
- **Wrong-network recovery** — credit + reclaim funds paid on the wrong EVM chain,
  because one key controls the address across all EVM chains.
- **HMAC-SHA256** webhooks and request auth — vs epusdt's dated MD5(sorted-params+secret).
- **A tested idempotency ledger** — txid `(+ logIndex)` burned into a PRIMARY-KEY registry
  inside the crediting transaction, with unit tests proving replay-safety.

> Honest caveat: epusdt is battle-tested and simple, with a large fork ecosystem. Optimus
> is more advanced, but "advanced" always carries more surface area — run it, watch it,
> keep your seed offline.

---

## Honest trade-offs vs Cryptomus

Cryptomus is a different product class. By being self-hosted and non-custodial, Optimus
**deliberately forgoes** things Cryptomus does well:

- **Breadth** — 100+ assets incl. BTC/Solana/Litecoin/Doge and fiat pricing.
- **Fiat rails** — card on-ramp, fiat settlement/payouts, fiat-priced invoices.
- **Mass payouts, recurring/subscriptions, P2P/exchange, crypto cards, a mobile app.**
- **Zero infra** — Cryptomus hosts everything; Optimus is your VPS, your uptime, your
  RPC reliability, your backups.
- **Ready-made plugins** (WooCommerce/OpenCart/PrestaShop/WHMCS/…). Optimus gives you a
  REST API + Python library; you build integrations.
- **Compliance-as-a-service** — KYC/AML tooling and a regulated counterparty (a plus for
  some businesses).

The reason to pick Optimus anyway is the *cost* of those conveniences: a custodial
processor **holds your money and can freeze/return it**, requires **KYC**, restricts some
countries, charges a **fee**, and is **closed-source**. Optimus trades those features for
**self-custody, zero fees, no KYC, and no freeze risk.**

---

## Money-safety engineering (why "reliable" is earned, not asserted)

The core is built to be correct with real money:

- **Integer cents end-to-end** — `cents = raw // 10**(decimals-2)`; no floats in the ledger.
- **Idempotent crediting** — a normalized `(txid, logIndex)` is burned into a PRIMARY-KEY
  registry *inside the same transaction* that credits, so a re-scan / retry / replay can
  never double-credit.
- **Crash-safe block cursor** — advances only over blocks actually scanned, never on an
  RPC error, with a re-scan overlap; a crash causes a harmless re-scan, never a skip.
- **RPC-forgery hardening** — every returned log is re-verified against the contract +
  Transfer topic + destination, so a malicious/rotated public RPC can't fabricate a credit.
- **Credit-not-consume** — underpayments accumulate; the order flips to paid only when
  covered; overpayments are recorded, not lost.
- **Safe-by-default surface** — the merchant API refuses remote unauthenticated orders
  until you set a key; merchant `notify_url`s are SSRF-guarded (no private/loopback hosts,
  no redirect-following); webhooks are HMAC-SHA256 signed.

See [`docs/CHAINS.md`](docs/CHAINS.md) for the exact contracts/RPCs and the tests in
[`tests/`](tests/) for the money-path invariants.

---

## Bottom line

- **Most advanced?** Yes — *within the self-hosted, non-custodial stablecoin category.*
  Not against a custodial processor's raw feature breadth (a category Optimus doesn't
  enter). Claim it precisely.
- **Most reliable?** The money-ledger correctness is the strongest in its class and is
  unit-tested. A funded custodial platform still wins on raw uptime-at-scale; your
  reliability depends on your ops discipline.
- **Easier & faster?** The **easiest self-hosted gateway to stand up** (one process, no
  MySQL/Redis, a setup wizard that previews your addresses). A hosted SaaS is still lower-
  effort to *start* using — that's the nature of hosted vs self-hosted.

> **Optimus Payment Gateway — self-hosted, non-custodial USDT & USDC across 7 EVM chains
> + TON. Your keys, your addresses, zero fees, no KYC.**

*Comparisons reflect public documentation at time of writing; competitor products change.
Verify current specifics against each project's own docs.*
