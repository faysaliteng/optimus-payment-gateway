<div align="center">

# ⚡ Optimus Payment Gateway

**Self-hosted, non-custodial, multi-chain crypto payments for any bot or app.**

Accept **USDT / USDC** on **BSC (BEP20) · Ethereum (ERC20) · Polygon · TON** —
with per-order HD addresses, automatic sweeping to a cold wallet, wrong-network
recovery, optional Binance verification, and signed merchant webhooks.

*No processor. No custody. No KYC on your funds. Your keys, your coins.*

</div>

---

## Why this exists

Most "crypto payment" options either take custody of your money, charge a cut, or
are a single-chain toy. This gateway is the **battle-tested engine from a live shop
that has settled thousands of real deposits**, extracted into a clean, reusable
service anyone can self-host. You point it at a **watch-only xpub** and it does the
rest — a fresh address per order, on-chain watching, crediting, and (optionally)
auto-forwarding every payment to a cold wallet you alone control.

## Features

| | |
|---|---|
| 🔗 **Multi-chain** | USDT + USDC on BSC, Ethereum, Polygon; USDT on TON. One address space across EVM chains. |
| 🏦 **Non-custodial** | Funds land at addresses derived from *your* xpub. The server can be **watch-only** and hold **zero** private keys. |
| 🎯 **Per-order addresses** | Every order gets a unique HD address (`m/44'/60'/0'/0/i`) — clean reconciliation, no amount guessing. |
| 💱 **Amount-match mode** | Prefer one static address? Orders are matched by a unique cents amount instead. |
| ⛽ **Gas tank + auto-sweep** | Optionally forward incoming funds to a **cold main wallet** automatically; gas is paid from a tiny hot "gas tank". |
| 🌐 **Wrong-network recovery** | A buyer paid USDC on Ethereum when you quoted BEP20? Same key controls it — the gateway **credits them and sweeps it home**. |
| 🧾 **Exact-cents ledger** | All money math is integer cents. No float rounding, ever. |
| ♻️ **Idempotent & crash-safe** | Every txid is burned before crediting; the block cursor never skips. Re-scans can't double-credit. |
| 🔔 **Signed webhooks** | HMAC-SHA256 server-to-server callbacks with retries, plus a poll API and a hosted checkout page + QR. |
| 🅱️ **Binance verify (optional)** | Confirm a Binance Pay order id / deposit txid against your own account with a read-only API key. |
| 🧰 **One process** | SQLite + stdlib. No Redis, no MySQL, no Node. `pip install` and go. |

## How it works (60 seconds)

```
  merchant app                 Optimus Gateway                    chains
 ┌───────────┐  create order  ┌────────────────┐  eth_getLogs   ┌─────────┐
 │  your bot ├───────────────►│ reserve + HD   ├───────────────►│ BSC/ETH │
 │  or shop  │◄──────────────┤ derive address │◄───────────────┤ Polygon │
 └─────┬─────┘  addr + amount └───────┬────────┘   watcher       │  TON    │
       │                              │ credit (idempotent)      └─────────┘
       │        signed webhook        │ + optional auto-sweep ───► your COLD wallet
       │◄─────────────────────────────┘
```

1. Your app calls **`POST /api/v1/order/create`** with an amount + your order id.
2. The gateway derives a fresh address from your xpub and returns it (+ a hosted
   `checkout_url` with a QR).
3. The **watcher** sees the on-chain transfer, credits the order idempotently, and
   fires a **signed webhook** to your `notify_url`.
4. If auto-sweep is on, the **sweeper** forwards the funds to your cold wallet and
   pays gas from the gas tank.

## Quick start

```bash
git clone <this repo> "Optimus Payment Gateway" && cd "Optimus Payment Gateway"
pip install -r requirements.txt
cp .env.example .env
```

### ⚡ Easiest path: the Setup Wizard (no config files)

You don't have to touch `.env` at all. Set an admin password and open the **Setup
Wizard** in your browser — it does the rest:

```bash
OPG_ADMIN_PASSWORD=yourpassword python -m admin.app     # http://localhost:8001/setup
```

In the wizard you either **paste your xpub** (it validates it live and shows your
first addresses so you can confirm it's yours) or click **"Generate dedicated
wallet"** (it creates one, saves the spend key server-side in a locked file, and shows
your 12-word backup once). Then tick the networks you want, optionally set a cold
wallet for auto-sweep, and hit **Save**. Changes apply **instantly, no restart**.
That's the whole setup. Prefer files? The env-var route below works too:

**Get a watch-only xpub** from any wallet (Trust Wallet, MetaMask, Ledger, …) — see
[`docs/XPUB_GUIDE.md`](docs/XPUB_GUIDE.md) and the offline tool in
[`tools/bip39-standalone.html`](tools/bip39-standalone.html). Put it in `.env`:

```ini
OPG_GATEWAY_XPUB=xpub6C...              # watch-only, safe
OPG_ENABLED_METHODS=usdt_bep20,usdt_polygon
OPG_MERCHANT_API_SECRET=<long-random>
```

Run it:

```bash
python run.py checkxpub $OPG_GATEWAY_XPUB   # sanity-check the xpub + preview addresses
python run.py serve                          # API + watcher on :8000
# or: docker compose up -d
```

Create a payment:

```bash
curl -s localhost:8000/api/v1/order/create -H 'content-type: application/json' -d '{
  "api_key":"...", "signature":"...",
  "method":"usdt_bep20", "amount":25.00,
  "order_id":"INV-1001", "notify_url":"https://shop.example/webhook"
}' | jq
# -> { data: { pay_address, pay_amount, checkout_url, trade_id, ... } }
```

Show `pay_address` + `pay_amount` (or redirect to `checkout_url`), then wait for the
webhook. That's it.

## Enable auto-sweep to a cold wallet (optional)

```bash
python run.py newwallet          # creates a dedicated hot wallet; saves xprv to private/ (0600)
# fund address_0 (the "gas tank") with a little BNB / ETH / POL on each chain
```
```ini
OPG_GATEWAY_XPUB=<the account_xpub it printed>
OPG_AUTO_SWEEP=true
OPG_SWEEP_DESTINATION=0xYourColdMainWallet
```
Now every payment is auto-forwarded to your cold wallet, and any **wrong-network**
payment is auto-credited + recovered. Your cold wallet's seed is **never** on the server.

## Documentation

| Doc | What's inside |
|---|---|
| [`docs/XPUB_GUIDE.md`](docs/XPUB_GUIDE.md) | Get a watch-only xpub from any wallet (+ the offline bip39 tool) |
| [`docs/API.md`](docs/API.md) | Merchant REST API — endpoints, signing, payloads, error codes |
| [`docs/INTEGRATION.md`](docs/INTEGRATION.md) | Drop-in integration for a Telegram bot, a web shop, or any backend |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How the watcher, ledger, sweeper and recovery fit together |
| [`docs/SECURITY.md`](docs/SECURITY.md) | The key model, idempotency, threat model, hardening checklist |
| [`docs/CHAINS.md`](docs/CHAINS.md) | Supported networks, token contracts, gas tanks, adding a chain |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Docker, systemd, reverse proxy, backups |

Working examples live in [`examples/`](examples/).

## Compared to alternatives

This project takes the clean **merchant-facing model** popularised by projects like
[epusdt](https://github.com/GMWalletApp/epusdt) (create-order → address/amount →
signed webhook, no Redis needed) and pairs it with a **far more capable chain engine**:
watch-only **xpub per-order addresses** (not one shared address), a **gas-tank
auto-sweeper** to cold storage, **wrong-network recovery**, **TON memo** support, and
optional **Binance** cross-checking. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## ⚠️ Security

This software moves real money. Before mainnet:
- Keep your **seed offline**; give the gateway only the **xpub**.
- The auto-sweep **xprv** lives in a `0600` file — never in git, env, or logs.
- Your **cold main wallet** seed is never on the server.
- Read [`docs/SECURITY.md`](docs/SECURITY.md) and test on small amounts first.

MIT licensed, no warranty. You are responsible for your keys and your compliance.
