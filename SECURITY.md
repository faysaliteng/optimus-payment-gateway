# Security Policy

This project moves real cryptocurrency. We take security seriously and appreciate
responsible disclosure.

## Reporting a vulnerability

**Please do NOT open a public GitHub issue for security problems.**

Instead, report privately via **[GitHub Security Advisories](https://github.com/faysaliteng/optimus-payment-gateway/security/advisories/new)**
(Security → Report a vulnerability), or contact the maintainer **[@faysaliteng](https://github.com/faysaliteng)**
directly. Include steps to reproduce and impact. We aim to acknowledge within a few days.

Please give us reasonable time to release a fix before any public disclosure. We're happy
to credit you.

## What's in scope
- The payment ledger and idempotency (double-credit, replay, race conditions).
- HD derivation / key handling (any path that could leak a private key or xprv).
- The sweeper (unauthorized fund movement, gas-tank abuse).
- Merchant API auth + webhook signature verification.
- The admin panel auth.

## The security model (summary)
- The gateway is **non-custodial**: give it a **watch-only xpub** and it holds **no
  private keys**. The optional sweep key is a **dedicated** wallet's xprv, kept in a
  `0600` file — never in git, env, logs, or the database.
- Your **cold main wallet seed is never on the server**; the sweeper can only *send* to
  its address.
- Every on-chain reference is **burned before crediting** (idempotency), and RPC
  responses are **re-verified** so a malicious node can't forge a credit.

Full details and a hardening checklist: [`docs/SECURITY.md`](docs/SECURITY.md).

## Before you run it
Test on small amounts, keep your backup words offline, put only pocket change in the gas
tank, and put the admin panel behind HTTPS + a strong password. This software ships with
**no warranty** (MIT).
