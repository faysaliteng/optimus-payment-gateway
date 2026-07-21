<div align="center">

# ⚡ Optimus Payment Gateway

**Accept crypto payments yourself — no processor, no middleman, no one holding your money.**

USDT / USDC on **BSC · Ethereum · Polygon · Arbitrum · Optimism · Base · Avalanche · TON**,
plus native **Litecoin (LTC)** — with a click-through setup, automatic payment detection,
automatic forwarding to your cold wallet, and wrong-network recovery. Point it at your
wallet's **xpub** and it does the rest.

**➕ Also verify Binance Pay** — accept a buyer's **Binance Pay** order id (or a deposit
straight into your Binance account) and confirm it against your *real* Binance history with
a **read-only** key. Reference + amount + status + **receiver** are all checked, and every
payment — on-chain or Binance — burns through one **anti-replay lock** so it credits *exactly
once.* See [`docs/BINANCE.md`](docs/BINANCE.md).

[![Tests](https://github.com/faysaliteng/optimus-payment-gateway/actions/workflows/tests.yml/badge.svg)](https://github.com/faysaliteng/optimus-payment-gateway/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Non-custodial](https://img.shields.io/badge/custody-none%20(your%20keys)-orange.svg)](docs/SECURITY.md)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Stars](https://img.shields.io/github/stars/faysaliteng/optimus-payment-gateway?style=social)](https://github.com/faysaliteng/optimus-payment-gateway/stargazers)

**If this saves you from a custodial processor, please ⭐ star the repo — it helps others find it.**

</div>

> **New here? You're in the right place.** This README is written so a **complete
> beginner** can set it up — and so you can hand the whole thing to an AI (ChatGPT,
> Claude, etc.) and say *"add this to my app."* Jump to
> [Set it up in 6 steps](#-set-it-up-in-6-steps-beginner-friendly) or
> [Give this to your AI](#-give-this-to-your-ai).

---

## 🧠 What is this, in plain English?

Think of **Stripe or PayPal**, but for crypto — **except you keep 100% of the money
and there's no company in the middle.** You run a small program on a server (or your
PC). When someone owes you money, your app asks this gateway for a payment; the gateway
gives the buyer a wallet address to send USDT/USDC (or Litecoin) to. The moment the payment lands on
the blockchain, the gateway **notices it, confirms it, and tells your app "paid!"** —
and (if you want) **automatically moves the money to a safe "cold" wallet** you control.

**You never give anyone your secret keys.** The gateway only needs your **xpub** — a
"read-only" key that can *create receiving addresses* but **cannot spend a cent.**

**Prefer Binance Pay?** Some buyers would rather pay you on **Binance** than on-chain. The
gateway handles that too: the buyer pays your Binance Pay ID and submits their order id, and
the gateway checks it against your *own* Binance history (again, a **read-only** key) — that
the payment really landed, for the right amount, **on your account** — before your app credits
anyone. The same one-time-only lock that stops a blockchain payment counting twice stops a
Binance reference being replayed. Full guide: [`docs/BINANCE.md`](docs/BINANCE.md).

## ✅ What it does automatically vs. 🖐️ what you do

| The gateway does this **for you, automatically** | You do this **once** |
|---|---|
| 🟢 Creates a **fresh address for every order** (from your xpub) | 🖐️ Paste your **xpub** (or click *Generate wallet*) |
| 🟢 **Watches the blockchain** 24/7 for incoming payments | 🖐️ Tick which **networks** you accept |
| 🟢 **Confirms & credits** each payment (no double-counting) | 🖐️ Set your **cold wallet** (where funds collect) |
| 🟢 Sends your app a **signed "paid" webhook** | 🖐️ Put a little **gas** in the gas tank |
| 🟢 **Auto-collects** every payment into your one cold wallet ("auto-sweep") | 🖐️ Flip **auto-sweep** on |
| 🟢 **Recovers wrong-network** payments (paid on ETH by mistake, etc.) | |
| 🟢 **Verifies Binance Pay** order ids / deposits against your real history (optional) | 🖐️ *(optional)* add a **read-only** Binance key + your **Pay ID** |

### 💰 Getting all payments into ONE wallet
Because each order is paid to its **own** address (that's what keeps things private and
easy to reconcile), the gateway has to *forward* them to collect everything in one place —
and **moving crypto always costs a little gas.** So to auto-collect into a single wallet:

1. Set your **cold wallet** as the destination, 2) put a few dollars of **BNB/ETH/POL** in
the gas tank, 3) turn on **auto-sweep**. Then every payment lands in your one wallet
automatically. **This is required for one-wallet collection** — not a nice-to-have.

**Don't want to deal with gas at all?** Use **one shared receiving address** instead of
per-order addresses (set your own wallet as the receiver in Setup → *Advanced*). Then
every payment goes **straight to that one wallet** with **no gas and no sweeping** — the
trade-off is that one address is reused/public and buyers pay an exact unique amount.

*(Receiving + crediting buyers needs no gas either way — gas is only for moving the
collected funds.)*

> 💡 **The one honest catch:** the gateway **can't create gas out of thin air.** Moving
> crypto costs a tiny network fee ("gas"), paid in the chain's coin (BNB on BSC, ETH on
> Ethereum, POL on Polygon). So to enable *auto-forwarding*, you send a few dollars of
> that coin to the gas-tank address the app shows you. That's the only "manual" bit, and
> the wizard walks you through it. **Receiving + crediting buyers needs no gas at all.**

---

## 🪙 Supported networks & tokens

Accept **USDT and USDC** across **7 EVM chains + TON**, plus native **Litecoin** — more
networks than most popular gateways, all from watch-only extended public keys. Enable any
subset (the Setup wizard shows them as checkboxes; or set `OPG_ENABLED_METHODS`).

| Network | Chain ID | Tokens accepted | Gas coin | Auto-sweep | Notes |
|---|---|---|---|---|---|
| **BSC (BEP20)** | 56 | USDT · USDC | BNB | ✅ | ⭐ cheapest fees for buyers |
| **Polygon** | 137 | USDT · USDC · USDC.e | POL | ✅ | ⭐ near-zero fees |
| **Arbitrum** | 42161 | USDT · USDC · USDC.e | ETH | ✅ | L2, low fees, fast |
| **Optimism** | 10 | USDT · USDC · USDC.e | ETH | ✅ | L2, low fees |
| **Base** | 8453 | USDC · USDT · USDbC | ETH | ✅ | L2, fast-growing |
| **Ethereum** | 1 | USDT · USDC | ETH | ✅ | maximum compatibility |
| **Avalanche C-Chain** | 43114 | USDT · USDC · .e | AVAX | ✅ | fast finality |
| **TON** | — | USDT (jetton) | TON | manual¹ | Telegram-native, memo-routed |
| **Litecoin (LTC)** | — | native LTC | LTC | adapter² | 🪙 **native coin, per-input sweep fee ~fractions of a cent — no gas tank**; credited at USD value. [`docs/LITECOIN.md`](docs/LITECOIN.md) |

<sub>All EVM tokens are the **official Circle-issued USDC** and **Tether USDT** contracts
(6-decimal on L2s, 18-decimal on BSC), with popular **bridged** variants (USDC.e / USDbC /
USDT.e) also watched so no payment is missed. Every contract address was cross-verified on
the chain's official explorer — see [`docs/CHAINS.md`](docs/CHAINS.md). ¹TON uses a shared
memo address, so funds already arrive in one wallet. ²Litecoin ships as a **tested adapter**
(watch-only BIP84 derivation + a BIP143 P2WPKH signer + USD pricing + a consolidating sweep in
`ltc.py`) that you wire into your own poll loop — the bundled server auto-runs the EVM + TON
rails; LTC is a library, not yet auto-scanned. See [`docs/LITECOIN.md`](docs/LITECOIN.md).</sub>

**Because every EVM chain shares one address space, the same xpub serves all of them** —
one setup covers BSC, Ethereum, Polygon, Arbitrum, Optimism, Base and Avalanche, and a
buyer who pays on the "wrong" one is auto-recovered. Adding another EVM chain is a
~5-line registry entry ([`docs/CHAINS.md`](docs/CHAINS.md)).

> **Method keys** (for `OPG_ENABLED_METHODS` / the API `method` field): `usdt_bep20`,
> `usdt_polygon`, `usdt_arbitrum`, `usdt_optimism`, `usdt_base`, `usdt_erc20`,
> `usdt_avalanche`, `usdt_ton`, and `ltc` (native coin, credited at USD value — configured
> separately, see below). *(Tron/TRC-20 uses a non-EVM address format and isn't in this build
> yet — it's on the roadmap.)*

## 📋 What you need before you start

1. **A wallet you control** that can give you an **xpub** (Trust Wallet, MetaMask, a
   Ledger, or just the free offline tool included in `tools/bip39-standalone.html`).
   *Don't have one / not sure? The setup wizard can **generate a fresh wallet for you in
   one click.** You'll write down 12 backup words and you're done.*
2. **Somewhere to run it:** any computer or cheap cloud server (a $5/month VPS is plenty)
   with **Python 3.10+**, or **Docker**. It can even run on your own PC to try it out.
3. **~15 minutes.** No coding required for setup.

---

## 🚀 Set it up in 6 steps (beginner-friendly)

### Step 1 — Get the code and install it
**Get the files** — either clone with git, or download the ZIP and unzip it. Then open
a terminal **inside that folder** and install:
```bash
# from inside the project folder:
python -m venv .venv                 # optional but recommended (a clean sandbox)
# activate it:  Windows: .venv\Scripts\activate   ·   macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
```
> If `python` says "not found", try `python3`. On Windows, `python` is correct.

*Prefer Docker?* First create your config file, set an admin password in it, then start:
```bash
copy .env.example .env      # Windows   (macOS/Linux:  cp .env.example .env)
# open .env and set OPG_ADMIN_PASSWORD=something , then:
docker compose up -d
```
With Docker the panel is already running at **http://localhost:8001/setup** — jump to Step 3.

### Step 2 — Open the Setup panel
Pick any password and start the admin panel. **Use the line for your system:**
```powershell
# Windows PowerShell:
$env:OPG_ADMIN_PASSWORD="choose-a-password"; python -m admin.app
```
```bat
:: Windows CMD:
set OPG_ADMIN_PASSWORD=choose-a-password && python -m admin.app
```
```bash
# macOS / Linux:
OPG_ADMIN_PASSWORD=choose-a-password python -m admin.app
```
Now open **http://localhost:8001/setup** in your browser. Your browser will pop up a
**username / password box** — enter `admin` and the password you chose. You'll land on
the **Setup Wizard**.

> Tip: instead of setting the password each time, you can `copy .env.example .env`
> (macOS/Linux `cp`), put `OPG_ADMIN_PASSWORD=...` in that `.env` file, and it's picked
> up automatically on every run.

### Step 3 — Add your wallet (this is the main step)
You have two easy options:

- **Option A — I have a wallet:** paste your **xpub** into the box and click
  **"Validate & preview addresses."** The wizard shows your first few addresses so you
  can **confirm they match your wallet** (address #1 here should equal address #1 in
  your wallet app). ✔️ Done.
- **Option B — Make me a new one:** click **"⚡ Generate dedicated wallet."** The app
  creates a brand-new wallet just for payments, **shows you 12 backup words once**
  (write them on paper, keep them safe), and sets everything up automatically. ✔️ Done.

> 🔒 Either way, **your secret/seed is never uploaded or stored in a way that can spend
> your main money.** Option A uses a *watch-only* xpub. Option B's spend key is saved in
> a locked file on *your* server only. Never paste your **seed phrase** anywhere online.

### Step 4 — Choose your networks
Tick the chains you want to accept (e.g. **BEP20** and **Polygon** — these have the
cheapest fees for buyers). Optionally tick **"Also accept USDC."** Hit **Save**. 🎉
**Your settings are saved and live.**

> ℹ️ Two separate programs: the **panel** (`:8001`) only *configures* things; the
> **payment service** (`:8000`, started in Step 6 with `python run.py serve`) is what
> actually watches the blockchain and detects payments. Keep the service running for
> live payments.
>
> ⚠️ Payment detection needs a blockchain node that allows log-reading. The built-in
> defaults work, but the official `bsc-dataseed` nodes **don't** — if BSC payments
> aren't detected, set a working RPC (see [Troubleshooting](#️-troubleshooting)).

### Step 5 — Collect all payments into one wallet (auto-sweep)
Each order is paid to its own address, so to gather **everything into your single cold
wallet** automatically (required for one-wallet collection), turn on auto-sweep:
1. Make sure you used **Option B** (or otherwise have a dedicated wallet) so the server
   can sign the transfers.
2. Enter your **cold wallet address** ("sweep destination").
3. Click **"Check balances"** under *Gas tanks*. It shows **one gas-tank address** (the
   same address on every EVM chain). **Send a few dollars of the native coin on each
   network to that same address** — BNB on BSC, ETH on Ethereum, POL on Polygon — so the
   gateway can pay the tiny network fee when it forwards your money.
4. Tick **"Enable auto-sweep"** and **Save.**

Now incoming payments are credited to your app **and** auto-forwarded to your cold
wallet within minutes. If a buyer ever pays on the wrong network, the gateway **finds
it and recovers it automatically** too.

### Step 6 — Test it (do this before real customers!)
In a terminal, **start the payment service** (this is the part that watches the chain):
```bash
python run.py serve            # runs the API + watcher on http://localhost:8000
```
Leave that running. In a **second** terminal, create a tiny test payment — **use the
line for your system:**
```powershell
# Windows PowerShell:
Invoke-RestMethod -Uri http://localhost:8000/api/v1/order/create -Method Post -ContentType 'application/json' -Body '{"method":"usdt_bep20","amount":1.00,"order_id":"TEST-1"}'
```
```bash
# macOS / Linux (or curl.exe on Windows):
curl -s http://localhost:8000/api/v1/order/create -H 'content-type: application/json' \
  -d '{"method":"usdt_bep20","amount":1.00,"order_id":"TEST-1"}'
```
The reply is wrapped in a `data` object:
```json
{ "status_code": 200,
  "data": { "trade_id": "…", "pay_address": "0x…", "pay_amount": "1.00",
            "checkout_url": "http://localhost:8000/pay/…", "status": "pending" } }
```
Open the **`data.checkout_url`** in a browser, send $1 of USDT on BEP20 to the address
shown, and watch the page flip to **"✔ Payment received."** That's your gateway working
end-to-end. ✅

> Testing webhooks locally? By default the gateway **refuses `notify_url`s that resolve to
> private/loopback addresses** (an SSRF guard — it won't be tricked into calling
> `169.254.169.254`, `localhost`, `10.x`, …). So for a local test either **poll**
> `GET /api/v1/order/{trade_id}` (its `data.status` becomes `paid`), expose your app with a tunnel
> like **ngrok** and use that `https://…` URL, or set `OPG_ALLOW_PRIVATE_WEBHOOKS=true` (**dev only**).

---

## 💸 How money flows (the whole thing on one screen)

```
  Your app         Optimus Gateway                Blockchain            You
 ┌────────┐  1.create  ┌──────────────┐                              ┌────────┐
 │ shop / ├──────────► │ makes a fresh │                              │  COLD  │
 │  bot   │ ◄──────────┤ address+amount│                              │ WALLET │
 └───┬────┘  addr,QR   └──────┬───────┘                              └───▲────┘
     │  2. show to buyer      │ 3. watches ──► sees payment ──► credits │
     │                        │ 4. "PAID" webhook ─────────────────────►│  auto-sweep
     │ ◄──────────────────────┘                                         │ (optional)
     ▼ mark order paid
```

1. Your app asks for a payment → gets an **address + exact amount** (and a ready-made
   **checkout page with a QR**).
2. You show that to the buyer.
3. The buyer pays; the gateway **sees it on-chain and credits it** (safely, once).
4. The gateway **notifies your app** ("paid") and — with auto-sweep on — **forwards the
   money into your one cold wallet** (paying gas from the gas tank).

*(This shows per-order EVM mode. TON uses one shared address + a per-order memo; the LTC adapter
uses per-order LTC addresses; shared-address mode matches by exact amount; and the **Binance rail**
below verifies a Pay order id instead of watching the chain.)*

### ⏱️ When does it flip to "paid", and what about wrong amounts?

- **Confirmations:** an order flips to `paid` after enough block confirmations — per chain:
  **BSC 12 · Ethereum 6 · Polygon 20 · Arbitrum/Optimism/Base 5 · Avalanche 4** (override globally
  with `OPG_MIN_CONFIRMATIONS`, or per method with the `confirmations_<method>` setting; raise it for
  large amounts). So a payment is typically credited within seconds-to-minutes of landing.
- **Underpaid?** The order stays `pending` and `received_cents` shows the progress — the buyer can
  send the rest in **any number of transfers**; it flips to `paid` the moment the total covers
  `pay_amount_cents`.
- **Overpaid?** Still `paid`, with `overpaid_cents` reported so you can refund or credit the extra.
- **A late deposit after it's already paid** is recorded as a **top-up** (and still swept in
  per-order mode).

### 🧰 Command-line tools

```bash
python run.py serve             # the payment service: API + watcher + sweeper + webhook sender
python run.py checkxpub XPUB    # validate an xpub + preview its first 5 addresses (do this before going live)
python run.py newwallet         # generate a dedicated hot wallet for auto-sweep (non-wizard path)
python run.py tanks             # show gas-tank balances per chain
python run.py recover           # one-shot: credit + sweep any wrong-network funds now
```

### 🖥️ Admin dashboard (optional second process)

`python -m admin.app` (port **8001**, HTTP Basic auth, **disabled unless `OPG_ADMIN_PASSWORD` is
set**) gives you the browser **Setup Wizard** (validate/preview an xpub, one-click generate a
dedicated wallet, pick chains + sweep destination, all saved live with **no restart**) plus a
read-mostly **dashboard**: order KPIs, a filterable orders table, per-order detail, live gas-tank
balances with low-fuel warnings, and a one-click **wrong-network recover**. It's a separate Flask
app from the core service — run it only while configuring, or keep it up for monitoring.

---

## 🔌 Connect it to your app / bot

**Two ways** — pick whichever fits:

**A) As a Python library** (simplest if your app is Python):
```python
from optimus_gateway import init, create_payment, get_payment
init()
order = create_payment("usdt_bep20", 25.00, merchant_order_id="INV-1001",
                        notify_url="https://your-app.com/webhook")
print(order["pay_address"], order["pay_amount"], order["checkout_url"])
```

**B) Over the REST API** (works with any language/framework):
```bash
POST http://your-gateway:8000/api/v1/order/create
{ "method":"usdt_bep20", "amount":25.00, "order_id":"INV-1001",
  "notify_url":"https://your-app.com/webhook" }
```
The response wraps the result in a **`data`** object — read `data.pay_address`,
`data.pay_amount`, `data.checkout_url`, `data.trade_id`. When it's paid, we POST a
**signed webhook** to your `notify_url` — verify it (see the snippet below). With no
`OPG_MERCHANT_API_KEY` set, create-order is accepted **only from localhost** (safe for local
dev; remote callers get `403`); set `OPG_MERCHANT_API_KEY` + `OPG_MERCHANT_API_SECRET` before
exposing it publicly, and each request then needs `api_key` + an HMAC `signature`. Full details:
[`docs/API.md`](docs/API.md) · [`docs/INTEGRATION.md`](docs/INTEGRATION.md) · examples in
[`examples/`](examples/).

**Verify the "paid" webhook** (the single most safety-critical step — same signing scheme as requests):
```python
import hmac, hashlib
def sign_params(secret, body):                       # canonical sorted k=v&… (skip signature/sign + empties)
    payload = "&".join(f"{k}={body[k]}" for k in sorted(body)
                       if k not in ("signature", "sign") and body[k] not in (None, ""))
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

def handle_webhook(json_body, header_sig, secret):   # header = X-OPG-Signature
    if not hmac.compare_digest(header_sig or "", sign_params(secret, json_body)):
        return 403                                   # forged / mismatch → reject
    if json_body.get("status") == "paid":            # terminal + idempotent: credit the trade_id ONCE
        mark_paid(json_body["trade_id"])
    return 200
```
No webhooks in your setup? Poll `GET /api/v1/order/{trade_id}` and act on `data.status == "paid"`.

### 🤖 Give this to your AI

Setting up your own bot/shop and want an AI to wire this in **end-to-end, nothing missed**?
**Attach `docs/API.md`, `docs/INTEGRATION.md`, `docs/BINANCE.md`, `docs/SECURITY.md`, and the
`examples/` folder to the chat**, then paste this advanced prompt to ChatGPT / Claude / Cursor:

> **Context.** I'm integrating the **Optimus Payment Gateway** — a self-hosted, non-custodial
> payment gateway (files attached). It runs as my own service; I keep 100% of funds and my keys
> never leave me. It gives me **two rails** and I want **both** wired in: **(A) on-chain crypto**
> via its HTTP merchant API, and **(B) Binance Pay** via its verification library. **Read the
> attached docs first; do not invent endpoints, fields, or signatures.**
>
> **(A) On-chain crypto — HTTP merchant API** (base URL = where I host it):
> - **Create a payment:** `POST /api/v1/order/create`, body `{ method, amount, order_id, notify_url }`.
>   `method` is a network key returned by `GET /health` → `enabled_methods` (e.g. `usdt_bep20`,
>   `usdt_erc20`, `usdt_polygon`, `usdt_ton`, `ltc`). **Send `amount` as a STRING** (`"25.00"`) —
>   signing is over the exact string.
> - **Auth:** if `OPG_MERCHANT_API_KEY` is set (required in production), every create call must add
>   `api_key` + a `signature` = HMAC-SHA256 over the sorted `k=v&…` of the body (excluding
>   `signature`/`sign`) with my secret. Worked example is in `docs/API.md` — copy it exactly.
> - **Response is wrapped:** `{ status_code, data: { trade_id, merchant_order_id, method, pay_address,
>   pay_amount, pay_amount_cents, status, expires_at, checkout_url, pay_memo? } }` (`merchant_order_id`
>   is the echo of my request `order_id`; `pay_memo` is non-null only on TON). Everything real is under `data`.
> - **Show the buyer `data.checkout_url`** (hosted page: QR + address + live status), **or** render
>   `data.pay_address` + `data.pay_amount` myself. **Always display `pay_amount`, never my original
>   quote** — in shared-address mode it's nudged a cent so the amount identifies the order; the buyer
>   must send that exact amount. For `usdt_ton`, also show `data.pay_memo` (the buyer MUST include it).
> - **Settlement:** when paid, the gateway POSTs a **signed webhook** to my `notify_url`. **Verify it**
>   with the SAME scheme as request signing (see `docs/API.md` → "Verifying a webhook"): recompute
>   HMAC-SHA256 over the canonical sorted `k=v&…` of the payload (**excluding** `signature`/`sign` and
>   empty values) with my secret, and compare **constant-time** to the `X-OPG-Signature` header; reject
>   on mismatch. Payload = `{ event, trade_id, merchant_order_id, method, status, amount_cents,
>   received_cents, pay_address, timestamp }`. `status == "paid"` (event `payment.completed`) is my
>   settlement signal.
> - **No webhooks?** Poll `GET /api/v1/order/{trade_id}` and act on `status == "paid"`.
> - **Partial / overpayment:** `received_cents` accumulates across transfers; the order flips to
>   `paid` only once it covers `pay_amount_cents`; an overpayment leaves `received_cents` higher —
>   handle both gracefully.
>
> **(B) Binance Pay — verification library** (server-side, **not** the HTTP API; see `docs/BINANCE.md`).
> Some buyers pay my Binance Pay ID and submit their **order id**; I verify it against my own Binance
> history with a **read-only** key:
> ```python
> from optimus_gateway.binance import BinanceAccount, BinanceVerifier
> res = BinanceVerifier(BinanceAccount.from_config()).verify_and_claim(order_id, expected_amount=amount)
> if res["ok"]:
>     credit_user(res["amount"])            # verified AND the reference is now burned
> elif res["reason"] == "already_used":
>     pass                                  # replay — do NOT credit
> ```
> `verify_and_claim` checks the reference exists, the amount, a success status, **and that it paid
> *my* Pay ID**, then burns it so it can't be replayed. (Marketplace with per-seller payouts? Use one
> `BinanceAccount(api_key=…, api_secret=…, pay_id=…)` per seller.)
>
> **Non-negotiable safety rules (apply to BOTH rails):**
> 1. **Never credit or deliver before verification passes** — the webhook signature (A) or
>    `verify_and_claim` returning `ok` (B).
> 2. **Be idempotent.** A webhook can be re-delivered and a reference re-submitted. Treat
>    `status == "paid"` as terminal and never credit the same `trade_id`/reference twice (the gateway
>    already burns txids/references end-to-end; mirror that on my side).
> 3. **Auth in production:** with no `OPG_MERCHANT_API_KEY`, create-order is refused for non-localhost
>    callers — but behind a reverse proxy every request *looks* local, so **always set
>    `OPG_MERCHANT_API_KEY` + `OPG_MERCHANT_API_SECRET`** before exposing it, and use an **HTTPS**
>    `notify_url` (the gateway refuses `notify_url`s that resolve to private/loopback IPs unless
>    `OPG_ALLOW_PRIVATE_WEBHOOKS=true`).
>
> **Task:** add a **"Pay with crypto"** (and, if I use Binance, **"Pay with Binance"**) flow to my
> **[describe your app / bot / shop and stack]**: create the order, show the buyer the checkout, and
> on verified payment mark it paid + fulfil. Follow `docs/INTEGRATION.md` and the code in `examples/`,
> and call out anything I still need to configure.

---

## 🅱️ Binance Pay & manual verification (the second rail)

Not every buyer pays on-chain. Many pay you through **Binance Pay** (P2P/merchant) or
send an **on-chain deposit straight into your Binance account**. This gateway can verify
those too — against your *real* Binance history, using a **read-only** API key that can
never withdraw. The buyer submits their **order id** (or txid), you confirm it, and only
then credit them.

A payment verifies **only if every check passes**: the reference exists in your history
(matched across every id field Binance uses), the status is a success state, the amount
matches (exact / 2-dp / small tolerance, non-stable assets spot-converted to USDT), and —
crucially — **the payment landed on *your* Pay ID** (so a buyer can't submit a real order
id that actually paid someone else). Optional min-age guard, and an automatic deep search
(up to ~18 months) if the recent scan misses it.

```python
from optimus_gateway.binance import BinanceAccount, BinanceVerifier

v = BinanceVerifier(BinanceAccount.from_config())      # or per-seller creds
res = v.verify_and_claim("443746280424488960", expected_amount=4.00)
if res["ok"]:
    credit_user_wallet(res["amount"])                  # reference is now BURNED — can't replay
elif res["reason"] == "already_used":
    ...                                                # someone re-submitted it → do NOT credit
```

`verify_and_claim` ties verification to the **anti-replay reference registry** — the same
lock every on-chain rail uses — so a reference credits **exactly once**. It works
per-platform *or* one account per seller (isolated "Direct Payment" mode), handles the
Binance Pay **merchant webhook** (HMAC-SHA512, fail-closed), and ships a CLI:

```bash
python examples/binance_manual_check.py 443746280424488960 --amount 4.00 --deep
```

Enable it with `OPG_BINANCE_ENABLED=true` + a read-only key + your `OPG_BINANCE_PAY_ID`.
**Full guide, threat model & the golden order-of-operations: [`docs/BINANCE.md`](docs/BINANCE.md).**

---

## ⛽ Understanding the gas tank (30-second read)

- Sending crypto costs a **tiny network fee** paid in the chain's own coin: **BNB** on
  BSC, **ETH** on Ethereum, **POL** on Polygon.
- The gateway keeps a small **"gas tank"** (one address, same on all EVM chains) and pays
  those fees from it when it forwards your money to cold storage.
- **You top it up** with a few dollars of each coin. A BEP20 forward costs a fraction of
  a cent; a Polygon one is basically free; Ethereum is the priciest (cents to a couple
  dollars when busy).
- **Empty tank? No problem** — buyers are still credited instantly; the money simply
  waits at its address until you add gas, then it sweeps automatically.
- The panel's **Dashboard** shows each tank's balance and warns you when one is low.

**Gas is ONLY needed to move money to your cold wallet. Receiving payments needs none.**

### 💡 Selling cheap items? Two gas-savers

EVM sweep gas is a **fixed cost per forward**, so consolidating a $0.50 order can cost more
than it's worth. Two features fix that:

- **Litecoin** — the sweep fee is a **few hundred litoshis per input** (fractions of a US cent,
  paid from the LTC itself — no gas tank), so small orders keep essentially all their value. Ideal
  for sub-$1 products. Note LTC is set up **separately from the EVM wizard**: it needs its own
  watch-only BIP84 zpub (`OPG_LTC_XPUB`), `OPG_LTC_ENABLED=true`, and a **live LTC/USD price feed**
  (deposits credit at USD value — with no rate they credit **$0**); it's a tested adapter you wire
  into your app's poll loop. See [`docs/LITECOIN.md`](docs/LITECOIN.md).
- **Accumulating address pool** (EVM, opt-in) — reuse a small pool of addresses so many
  buyers' small payments **pile up on one address** and sweep **once** at a `$` threshold,
  amortizing gas. A **fully-paid** address is reused immediately (its funds are already
  confirmed) — filling the *fullest* address toward the threshold first, so it sweeps sooner;
  an address is locked while its order is pending, and only *un-paid* ones wait a safety
  cooldown. Attribution is always scoped to the open buyer. It's a live **DB setting**, not an env
  var — off by default (`pool_enabled=false`); set `pool_enabled=true` (and optionally `pool_size`,
  default 30) to enable. Off = byte-for-byte the original never-reuse behaviour.
  Full design + safety notes: [`docs/ADDRESS_POOL.md`](docs/ADDRESS_POOL.md).

## 🔐 Watch-only vs. dedicated wallet (your safety choice)

| | **Watch-only (Option A)** | **Dedicated wallet (Option B)** |
|---|---|---|
| What you give it | just your **xpub** | it generates a fresh wallet |
| Private keys on the server | **none** 🔒 | one **dedicated** key, in a locked file |
| Auto-forward to cold wallet | ❌ (you sweep by hand) | ✅ automatic |
| Best for | maximum safety | hands-off automation |

Both are non-custodial. Your **main wallet's seed is never on the server** either way —
it's only ever the *destination* funds get swept to.

---

## 🥇 Why Optimus — an honest, defensible comparison

**The precise claim:** Optimus has the **broadest stablecoin coverage of any self-hosted,
non-custodial, zero-fee payment gateway we know of** — USDT/USDC across **7 EVM chains + TON** from
a single watch-only xpub (plus a tested native-Litecoin adapter). It's a *different category* from
custodial processors (Stripe, PayPal, Coinbase Commerce, BitPay, Cryptomus) and a generation ahead
of the open-source stablecoin alternative (epusdt). The one self-hosted rival worth a serious
comparison is **BTCPay Server** — and where BTCPay is better, we say so.

Every capability marked for Optimus is backed by code in this repo (not marketing) and the money
path is covered by **76 passing tests**. Competitor cells reflect each product's public docs/pricing
at the time of writing — verify current specifics on their own sites.

| Capability | **Optimus (this repo)** | BTCPay Server | Cryptomus | NOWPayments | Coinbase Commerce | BitPay | epusdt | Stripe / PayPal |
|---|---|---|---|---|---|---|---|---|
| **Custody of funds** | **Non-custodial** — watch-only xpub = **0 spendable keys**; auto-sweep adds a dedicated hot key (bounded, never your cold seed) | Non-custodial | **Custodial** | Non-custodial* (hosted 3rd-party) | Non-custodial (onchain protocol) | **Custodial** | Non-custodial | **Custodial** |
| **Can freeze your money** | **No** | No | Yes | Limited | Limited | Yes | No | Yes |
| **Self-hostable** | **Yes** — one core process, SQLite/WAL | Yes (node + NBXplorer + Postgres) | No | No | No | No | Yes (Go + MySQL + Redis) | No |
| **Open source** | **Yes (MIT)** | Yes (MIT) | No | No | No | No | Yes | No |
| **Fees beyond gas** | **0%** | 0% | ~0.4–2% | ~0.5% | ~1% | ~1% | 0% | ~2.9% + fixed |
| **KYC / account** | **None** | None | Yes | Account | Coinbase acct | Business KYC | None | Business verification |
| **USDT/USDC (+bridged)** | **First-class, 7 EVM + TON** | Limited (BTC-first) | Yes | Yes | Limited | Limited | **TRC-20 only** | On-ramp only |
| **Bitcoin / Lightning** | No | **Yes (core)** | BTC | BTC | BTC | **BTC + LN** | No | No |
| **Fiat / cards** | No | No | Yes | Partial | Yes | Yes | No | **Yes (core)** |
| **Per-order HD address** | **Yes** (`hdwallet.py`) | Yes | Platform-side | Platform-side | Platform-side | Platform-side | **No** (amount-match) | N/A |
| **Wrong-network recovery** | **Yes** (`sweeper.recover_wrongnet`) | N/A | No | No | No | No | No | N/A |
| **Binance Pay verification rail** | **Yes** — read-only, anti-replay (receiver-matched when your Pay ID is set) | No | No | No | No | No | No | No |
| **Webhook signing** | **HMAC-SHA256** | HMAC-SHA256 | MD5 | HMAC | HMAC-SHA256 | Signed | **MD5** | HMAC |
| **Tested idempotent ledger** | **Yes** — `(txid, logIndex)` PK burned in-txn, 76 tests | Mature/audited | — | — | — | — | No published proof | — |

<sub>*NOWPayments forwards funds to your wallet but is a hosted third party you route through.
Figures reflect public docs/pricing at time of writing.</sub>

### What genuinely sets Optimus apart

- **Non-custodial, and provably so in code.** In watch-only mode the server is given only an account
  xpub and `validate_xpub` actively rejects any private key you try to paste — **zero spendable keys
  on the box**; root it and an attacker can *watch* addresses but move nothing. Turn on auto-sweep and
  a *dedicated hot-wallet* key signs the forwards — exposure is bounded to that hot wallet + in-flight
  deposits, and **your cold/main seed is never on the server** either way. Custodial processors hold
  your money and can freeze it.
- **Stablecoins across 7 EVM chains + TON from one xpub** (USDT + USDC + bridged USDC.e/USDbC) — the
  exact gap BTCPay (Bitcoin-first) and epusdt (Tron-USDT only) leave open.
- **Wrong-network recovery.** Because one key controls the per-order address on *every* EVM chain, a
  buyer who pays on the wrong EVM network is auto-detected and swept home — a scenario single-chain
  (epusdt) and Bitcoin-first (BTCPay) designs don't face and don't handle. *(Needs auto-sweep on and
  the gas tank funded in each chain's native coin; a payment that lands after the order's window is
  still swept to cold storage but is credited manually.)*
- **A tested money ledger.** A normalized `(txid, logIndex)` is burned into a PRIMARY-KEY registry
  *inside the same transaction* that credits, so re-scans, retried webhooks, racing workers, and
  replays can't double-credit. The **on-chain stablecoin ledger is integer cents** (no float drift in
  the credited balance); webhooks are **HMAC-SHA256** (not MD5); `notify_url`s are SSRF-guarded; the
  API is safe-by-default (localhost-only until you set a key). epusdt still signs with MD5 and
  disambiguates payments only by amount.
- **A Binance Pay second rail** nothing else here offers — verify a buyer's Binance order id against
  *your own* history with a read-only key, and (when you set your Pay ID) receiver-matched so it can't
  be a payment to someone else, then replay-locked so it credits exactly once.
- **Zero fees, no KYC, no middleman, MIT** — you pay only the blockchain's own gas.
- **Lightweight self-host** — one core process on SQLite/WAL (plus an optional admin dashboard), vs
  BTCPay's full node + NBXplorer + Postgres or epusdt's MySQL + Redis.

### The honest caveat (who should use something else)

Optimus is **not** a fiat processor and does **not** do Bitcoin or Lightning.
- Need **cards, bank settlement, chargebacks, or recurring billing**? Use **Stripe / PayPal / BitPay /
  Coinbase Commerce**.
- Want **Bitcoin + Lightning**, a full store/POS, and a large audited community? Use **BTCPay Server**
  — it's more mature than this project.
- Want **100–300+ coins** and ready-made e-commerce plugins with zero ops? **Cryptomus / NOWPayments**
  are the pragmatic pick.
- Need **KYC/AML tooling or a regulated counterparty**? A hosted processor gives you compliance-as-a-service a self-hosted gateway can't.

And self-custody means the reliability is *your* ops discipline, not a vendor's SLA: run a solid VPS +
RPC, keep the sweep key `0600` and your cold seed offline, fund the gas tank modestly, and **test with
tiny amounts on each chain first.** Choose Optimus when self-custody, zero fees, no KYC, and no
freeze-risk on stablecoins are worth more to you than hosted breadth and hand-holding. Longer
head-to-head: [`COMPARISON.md`](COMPARISON.md).

---

## ❓ FAQ

**Do I need coding skills?** No — setup is all clicks in the wizard. Connecting it to
your own app needs a little code (or an AI), and there are copy-paste examples.

**Does it take a fee / cut?** No. It's your software and your wallet. You pay only the
blockchain's own network fees.

**Is it custodial? Can it steal my money?** No. It uses a watch-only xpub (can't spend),
or a dedicated wallet whose only power is forwarding to *your* cold wallet.

**Which network should buyers use?** **BEP20 (BSC)** or **Polygon** — lowest fees for
them. It also accepts Ethereum and TON.

**What if a buyer sends the wrong coin or wrong network?** If it's a supported EVM chain
(BSC/ETH/Polygon), the gateway **auto-detects and recovers it.** Genuinely unsupported
coins should be avoided (tell buyers the exact network).

**Can I change settings later?** Yes — anytime in the Setup panel. Changes apply
instantly, no restart.

**Do I need Redis / a database server?** No. It uses a single SQLite file. One process.

## 🛠️ Troubleshooting

| Symptom | Fix |
|---|---|
| Payments not detected | Make sure the service is running (`python run.py serve`) and your RPC allows log-reading — use `https://bnb.api.onfinality.io/public`, **not** `bsc-dataseed`. |
| Remote `create-order` returns **403** | Keyless mode only allows localhost — set `OPG_MERCHANT_API_KEY` + `OPG_MERCHANT_API_SECRET` (recommended) and sign requests, or for a trusted network set `OPG_ALLOW_UNAUTHENTICATED=true`. |
| Webhook never fires to a `localhost` URL | The SSRF guard blocks private/loopback URLs — use a public `https://` (ngrok) `notify_url`, or set `OPG_ALLOW_PRIVATE_WEBHOOKS=true` (dev only). |
| Polygon payments missed | Public Polygon nodes rate-limit `eth_getLogs` — set a working Polygon RPC override (see [`docs/CHAINS.md`](docs/CHAINS.md)). |
| LTC deposit credited **$0.00** | No LTC/USD price feed configured — set a rate (`OPG_LTC_USD_RATE` / `set_ltc_rate_provider`); see [`docs/LITECOIN.md`](docs/LITECOIN.md). |
| Order stuck on **partial** | The buyer underpaid — they send the remainder (any number of transfers) and it flips to `paid`. |
| "No receiving wallet configured" | Finish Step 3 (add an xpub or generate a wallet) in the Setup panel. |
| Money credited but not swept | The gas tank is empty (fund the native coin **on that chain** — BNB can't pay ETH gas) or auto-sweep is off — see Step 5. |
| Admin panel says "disabled" | Set `OPG_ADMIN_PASSWORD` before starting `python -m admin.app`. |
| Preview address doesn't match my wallet | Your wallet may use a different account/path — use the offline tool with **Coin: ETH**, path `m/44'/60'/0'/0`. See [`docs/XPUB_GUIDE.md`](docs/XPUB_GUIDE.md). |

---

## 📚 Full documentation

| Doc | For |
|---|---|
| [`docs/XPUB_GUIDE.md`](docs/XPUB_GUIDE.md) | Getting your xpub/zpub from any wallet — incl. **Trust Wallet** — with the offline BIP39 tool |
| [`docs/LITECOIN.md`](docs/LITECOIN.md) | The low-fee **Litecoin** rail — setup, USD-value crediting, sweeps |
| [`docs/ELECTRUM_LTC.md`](docs/ELECTRUM_LTC.md) | Complete **Electrum-LTC** wallet setup, key export & maintenance |
| [`docs/ADDRESS_POOL.md`](docs/ADDRESS_POOL.md) | BEP20 address-reuse pool for gas-efficient small orders |
| [`docs/API.md`](docs/API.md) | The REST API + webhook signatures (for devs / AI) |
| [`docs/INTEGRATION.md`](docs/INTEGRATION.md) | Add crypto payments to your app in ~10 minutes |
| [`docs/BINANCE.md`](docs/BINANCE.md) | **Binance Pay + manual verification** (the second rail), the anti-replay lock & per-seller mode |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it works under the hood |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Key model, threat model, hardening checklist |
| [`docs/CHAINS.md`](docs/CHAINS.md) | Networks, token contracts, adding a chain |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Docker, systemd, HTTPS, backups |
| [`COMPARISON.md`](COMPARISON.md) | Honest comparison vs epusdt, Cryptomus & the field |

## ⚠️ Before real money

This software moves real crypto. Please: **test on tiny amounts first**, keep your
backup words **offline**, put only pocket-change in the gas tank, and read
[`docs/SECURITY.md`](docs/SECURITY.md). MIT licensed — free to use, modify, and sell,
with **no warranty**. You are responsible for your keys and your local laws.

---

## 🤝 Contributing & community

Contributions are very welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md). Found a bug or
have an idea? [Open an issue](https://github.com/faysaliteng/optimus-payment-gateway/issues).
Adding a chain, a wallet integration, or a language example? Send a PR. Security issue?
See [`SECURITY.md`](SECURITY.md).

## 👤 Author

**MD FAYSAL MAHMUD** — built and maintained by [@faysaliteng](https://github.com/faysaliteng).

If Optimus Payment Gateway is useful to you, the best thank-you is a **⭐ star** and
sharing it with someone who's tired of custodial processors.

## 📜 License

[MIT](LICENSE) © MD FAYSAL MAHMUD. Free to use, modify, and build on — commercially too.
