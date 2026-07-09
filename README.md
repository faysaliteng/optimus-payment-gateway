<div align="center">

# ⚡ Optimus Payment Gateway

**Accept crypto payments yourself — no processor, no middleman, no one holding your money.**

USDT / USDC on **BSC (BEP20) · Ethereum · Polygon · TON**, with a click-through setup,
automatic payment detection, automatic forwarding to your cold wallet, and
wrong-network recovery. Point it at your wallet's **xpub** and it does the rest.

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
gives the buyer a wallet address to send USDT/USDC to. The moment the payment lands on
the blockchain, the gateway **notices it, confirms it, and tells your app "paid!"** —
and (if you want) **automatically moves the money to a safe "cold" wallet** you control.

**You never give anyone your secret keys.** The gateway only needs your **xpub** — a
"read-only" key that can *create receiving addresses* but **cannot spend a cent.**

## ✅ What it does automatically vs. 🖐️ what you do

| The gateway does this **for you, automatically** | You do this **once** |
|---|---|
| 🟢 Creates a **fresh address for every order** (from your xpub) | 🖐️ Paste your **xpub** (or click *Generate wallet*) |
| 🟢 **Watches the blockchain** 24/7 for incoming payments | 🖐️ Tick which **networks** you accept |
| 🟢 **Confirms & credits** each payment (no double-counting) | 🖐️ *(optional)* Put a little **gas** in the gas tank |
| 🟢 Sends your app a **signed "paid" webhook** | 🖐️ *(optional)* Set your **cold wallet** address |
| 🟢 **Auto-forwards** funds to your cold wallet ("auto-sweep") | |
| 🟢 **Recovers wrong-network** payments (paid on ETH by mistake, etc.) | |

**So yes — to just *receive* money and get "paid" alerts, you literally only add an
xpub.** To also *auto-forward* everything to cold storage, you additionally fund the
gas tank and flip one switch. More on that below — it's easy.

> 💡 **The one honest catch:** the gateway **can't create gas out of thin air.** Moving
> crypto costs a tiny network fee ("gas"), paid in the chain's coin (BNB on BSC, ETH on
> Ethereum, POL on Polygon). So to enable *auto-forwarding*, you send a few dollars of
> that coin to the gas-tank address the app shows you. That's the only "manual" bit, and
> the wizard walks you through it. **Receiving + crediting buyers needs no gas at all.**

---

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

### Step 5 — *(Optional)* Turn on auto-forward to cold storage
If you want every payment automatically pushed to a safe wallet:
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

> Testing webhooks locally? The gateway can't reach `http://localhost` on your machine
> from itself over the internet, so for a local test either **poll**
> `GET /api/v1/order/{trade_id}` (its `data.status` becomes `paid`), or expose your app
> with a tunnel like **ngrok** and use that `https://…` URL as your `notify_url`.

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
4. The gateway **notifies your app** ("paid") and **optionally forwards the money to your
   cold wallet.**

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
**signed webhook** to your `notify_url` (verify the `X-OPG-Signature` header). If you set
`OPG_MERCHANT_API_KEY`, each request must include `api_key` + an HMAC `signature` (it's
open while that key is empty). Full details: [`docs/API.md`](docs/API.md) ·
[`docs/INTEGRATION.md`](docs/INTEGRATION.md) · examples in [`examples/`](examples/).

### 🤖 Give this to your AI

Setting up your own bot/shop and want an AI to wire this in? **Attach `docs/API.md`,
`docs/INTEGRATION.md`, and the `examples/` folder to the chat**, then paste this to
ChatGPT / Claude / Cursor:

> *"I'm using the **Optimus Payment Gateway** (a self-hosted, non-custodial crypto
> payment gateway — files attached). It exposes `POST /api/v1/order/create` (body:
> `method`, `amount`, `order_id`, `notify_url`); the JSON reply is wrapped as
> `{status_code, data:{ pay_address, pay_amount, checkout_url, trade_id, … }}` (fields
> under `data`). It's unauthenticated unless a merchant key is set — then requests need
> `api_key` + an HMAC-SHA256 `signature`. When an order is paid it POSTs a signed webhook
> to my `notify_url` (verify the `X-OPG-Signature` header via HMAC-SHA256 over the payload
> with my shared secret — see `docs/API.md`). Add a 'Pay with crypto' flow to my
> [describe your app]: create an order, show the buyer `data.checkout_url`, and mark the
> order paid when the verified webhook arrives. Follow `docs/INTEGRATION.md` and the code
> in `examples/`."*

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
| "No receiving wallet configured" | Finish Step 3 (add an xpub or generate a wallet) in the Setup panel. |
| Money credited but not swept | The gas tank is empty or auto-sweep is off — fund the tank (Step 5) and tick auto-sweep. |
| Admin panel says "disabled" | Set `OPG_ADMIN_PASSWORD` before starting `python -m admin.app`. |
| Preview address doesn't match my wallet | Your wallet may use a different account/path — use the offline tool with **Coin: ETH**, path `m/44'/60'/0'/0`. See [`docs/XPUB_GUIDE.md`](docs/XPUB_GUIDE.md). |

---

## 📚 Full documentation

| Doc | For |
|---|---|
| [`docs/XPUB_GUIDE.md`](docs/XPUB_GUIDE.md) | Getting your xpub from any wallet (with the offline tool) |
| [`docs/API.md`](docs/API.md) | The REST API + webhook signatures (for devs / AI) |
| [`docs/INTEGRATION.md`](docs/INTEGRATION.md) | Add crypto payments to your app in ~10 minutes |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it works under the hood |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Key model, threat model, hardening checklist |
| [`docs/CHAINS.md`](docs/CHAINS.md) | Networks, token contracts, adding a chain |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Docker, systemd, HTTPS, backups |

## ⚠️ Before real money

This software moves real crypto. Please: **test on tiny amounts first**, keep your
backup words **offline**, put only pocket-change in the gas tank, and read
[`docs/SECURITY.md`](docs/SECURITY.md). MIT licensed — free to use, modify, and sell,
with **no warranty**. You are responsible for your keys and your local laws.
