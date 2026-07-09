# How to get a watch-only xpub (for absolute beginners)

This gateway never needs — and should never be given — your secret recovery phrase.
It only needs your **xpub** (extended *public* key). This guide shows you, step by
step, how to get one from any wallet, verify it, and put it into the gateway.

If you just want the fastest, safest path and don't mind a brand-new wallet, skip to
[Option B: let the gateway make a dedicated wallet](#option-b--let-the-gateway-make-a-dedicated-wallet).

---

## 1. What is an xpub, and why is it safe to hand over?

When you create a crypto wallet you get a **seed phrase** (12/24 words). From that seed,
your wallet mathematically derives a tree of keys:

```
seed phrase (12 words)          <-- SECRET. Can spend everything. NEVER share.
   └─ account private key (xprv) <-- SECRET. Can spend this account. NEVER share.
        └─ account PUBLIC key (xpub)  <-- SAFE. Can only WATCH / generate addresses.
             ├─ address #0   0xAAA…   (index 0)
             ├─ address #1   0xBBB…   (index 1)
             ├─ address #2   0xCCC…   (index 2)
             └─ …
```

The magic of BIP32: an **xpub can generate an unlimited list of receiving addresses,
but it cannot produce the private keys behind them.** So a service holding your xpub can:

- ✅ derive a fresh address for every order (`0xBBB…`, `0xCCC…`, …), and
- ✅ watch the blockchain and see money arrive at those addresses,

but it **cannot**:

- ❌ move, spend, or steal any of that money, and
- ❌ recover your seed phrase or private keys from the xpub.

That is exactly what a payment gateway needs and nothing more. This is called
**watch-only** mode, and it is the recommended way to run Optimus. Funds land at
addresses that only *your* offline seed can spend.

> The gateway even refuses to accept a secret key by mistake: `validate_xpub()` in
> `optimus_gateway/hdwallet.py` **rejects** anything starting with `xprv/yprv/zprv/tprv`
> and only accepts `xpub/ypub/zpub/tpub`.

### Which path this gateway uses

Optimus derives EVM (BSC / Ethereum / Polygon) receiving addresses at:

```
m/44'/60'/0'/0/index      (BIP44, Ethereum coin-type 60, external chain)
```

- **index 0** is reserved for your main wallet / gas tank.
- **index 1, 2, 3, …** are the per-order receiving addresses the gateway hands out.

Because every EVM chain shares the same address format, **one xpub covers BSC,
Ethereum and Polygon at once** — that is also what makes wrong-network recovery possible.

---

## 2. Option A — export a watch-only xpub with the included offline tool (recommended)

The repo ships an **offline** copy of the well-known BIP39 tool at
[`tools/bip39-standalone.html`](../tools/bip39-standalone.html). It is a single HTML
file with no network calls — you can (and should) run it on a computer with the
internet physically disconnected.

### Step 2.1 — Open it with NO internet

1. Copy `tools/bip39-standalone.html` onto the machine you'll use (a USB stick is fine).
2. **Turn off Wi‑Fi / unplug the network cable.** (You can verify in your browser's
   DevTools → Network tab that nothing loads from the internet — the file is fully
   self-contained.)
3. Double-click the file to open it in any browser.

### Step 2.2 — Enter (or generate) your mnemonic

At the top of the page:

- To use an **existing** wallet: paste your 12/24-word phrase into the big
  **“BIP39 Mnemonic”** box.
- To create a **fresh** wallet just for payments: set **“Generate”** to *12* words and
  click the **Generate** button. **Write the new phrase down on paper and keep it
  offline** — this is the only backup of the funds you'll receive.

### Step 2.3 — Set the coin to ETH

Find the **“Coin”** dropdown (near the top, next to the mnemonic). Select
**`ETH - Ethereum`**. This sets the derivation to coin-type `60`, which is what all
EVM chains (BSC, Ethereum, Polygon) use.

### Step 2.4 — Copy the “Account Extended Public Key” (this is your xpub)

Scroll down to the **“Derivation Path”** section and make sure the **BIP44** tab is
selected. You'll see, near the top of that section:

```
Account Extended Private Key   xprv9s…   ← SECRET. Do NOT copy this one.
Account Extended Public Key    xpub6C…   ← THIS is what you want. Copy it.
BIP32 Derivation Path          m/44'/60'/0'/0
```

- Confirm the **BIP32 Derivation Path** field reads **`m/44'/60'/0'/0`**.
  (That is the account's external "change 0" chain; the gateway appends `/1`, `/2`, …
  to it for each order.)
- Click the copy icon next to **“Account Extended Public Key”** and copy the value that
  starts with **`xpub…`**.

Below that, the **“Derived Addresses”** table lists the actual addresses with paths
`m/44'/60'/0'/0/0`, `.../0/1`, `.../0/2`, … Note the address on the row whose path ends
in **`/1`** — you'll cross-check it in step 4.

> ⚠️ The row/field labelled **Account Extended *Private* Key** (`xprv…`) and every
> **private key** column in the address table are SECRETS. Never copy those anywhere.
> Only the **xpub** leaves this offline machine.

---

## 3. Notes for specific wallets

You do **not** have to use the tool above — if your wallet can export an
**Ethereum account xpub** directly, that works too. Support varies a lot:

| Wallet | Can it export an ETH account xpub? | How |
|---|---|---|
| **MetaMask** | Not from the UI. | MetaMask derives accounts along `m/44'/60'/0'/0/i` (Account 1 = index 0, Account 2 = index 1, …). Export your **Secret Recovery Phrase** and feed it to the offline tool in Option A. MetaMask's *“Account 2”* address will equal the tool's `/1` row — a handy sanity check. |
| **Trust Wallet** | Not directly. | Trust doesn't expose an xpub for EVM. Use its recovery phrase in the offline tool (Option A). |
| **Ledger Live** | Not from the UI, and beware the path. | Ledger Live uses a *different* layout for extra Ethereum accounts (`m/44'/60'/x'/0/0`, incrementing the **account'** hardened level), so its second account is **not** child `/1` of one xpub. Safest: dedicate a wallet to the gateway (Option B) or derive from a phrase in the offline tool and only ever use index 1,2,3 as receiving addresses. |
| **Electrum** | Yes, but it's a **Bitcoin** xpub. | *Wallet → Information → Master Public Key* gives an xpub/zpub — but Electrum is Bitcoin-only, and this gateway settles **EVM/TON**, so an Electrum BTC xpub is **not usable** here. Use Option A or B for an ETH-path key. |
| **Any BIP39 hardware/software wallet** | Effectively yes. | Take the 12/24-word phrase to the offline tool in Option A. |

**Rule of thumb:** if a wallet won't cleanly give you an `m/44'/60'/0'/0` account xpub,
use the offline tool (Option A) with that wallet's seed, or use Option B.

---

## 4. Verify the xpub before you trust it

From the gateway folder, run the built-in checker (this is read-only — it just derives
addresses from your public key):

```bash
python run.py checkxpub xpub6C...YOUR_XPUB
```

Expected output:

```
OK — watch-only xpub.
  index 1: 0xBBB…
  index 2: 0xCCC…
  index 3: 0xDDD…
  index 4: 0xEEE…
  index 5: 0xFFF…
```

**Cross-check:** the address printed for **`index 1`** must exactly match the address on
the **`m/44'/60'/0'/0/1`** row in the offline tool's *Derived Addresses* table (and, if
you used MetaMask, its *Account 2*). If they match, your xpub is correct and the gateway
will hand buyers addresses you fully control.

If instead you see `INVALID: that is a PRIVATE key …`, you copied the wrong field — go
back and copy the **public** (`xpub…`) key.

---

## 5. Put it in your `.env`

Open `.env` (copy it from `.env.example` first) and set:

```ini
# Watch-only account xpub (m/44'/60'/0'/0). Safe to store here.
OPG_GATEWAY_XPUB=xpub6C...YOUR_XPUB

# Which networks to accept
OPG_ENABLED_METHODS=usdt_bep20,usdt_polygon
OPG_ACCEPT_USDC=true
```

That's it. With `OPG_GATEWAY_XPUB` set, the gateway automatically switches into
**per-order-address mode**: each new order derives the next child address
(`index 1, 2, 3, …`) and watches the chain for a payment to it. You sweep the collected
funds yourself, whenever you like, using your offline seed — the server never holds a
spending key.

---

## 6. 🚨 The one warning that matters

> ### NEVER paste your SEED PHRASE, private key, or an `xprv` anywhere online — or into the gateway. ONLY the `xpub`.
>
> - The 12/24 **words** and any **`xprv…`** can **spend all your money**. Anyone who
>   gets them can drain you. Keep them on paper, offline, and give them to **no one**
>   and **no website** — including this gateway.
> - The gateway only ever needs `OPG_GATEWAY_XPUB` (the `xpub…`). If a field, form, or
>   person asks you for the seed or `xprv`, **stop** — that is not how this works.
> - The offline tool in step 2 is offline **on purpose**. Do the mnemonic → xpub step
>   with the network disconnected, copy only the `xpub`, then close the page.

---

## Option B — let the gateway make a dedicated wallet (enables auto-sweep)

If you'd rather not touch your personal wallet at all, generate a brand-new wallet that
exists only for the gateway. This also unlocks **automatic sweeping** to your cold
wallet:

```bash
python run.py newwallet
```

It prints three things:

```
Mnemonic (WRITE THIS DOWN OFFLINE, then delete from screen):
   <12 words>                          ← your offline backup of received funds

Account xpub (RECEIVING key — put in OPG_GATEWAY_XPUB):
   xpub6C…                             ← put this in .env

Gas tank / index-0 address (fund with a little native coin per chain):
   0xAAA…                              ← send a bit of BNB/ETH/POL here for gas
```

Then it asks whether to save the **spend key** (`xprv`) to a locked `0600` file
(`private/gateway_sweep/account.xprv` by default). Answer **`y`** only if you want the
gateway to auto-forward incoming payments to your cold wallet. Configure:

```ini
OPG_GATEWAY_XPUB=<the account_xpub it printed>
OPG_AUTO_SWEEP=true
OPG_SWEEP_DESTINATION=0xYourColdMainWallet    # your personal wallet; its seed is NEVER on the server
```

How the two modes compare:

| | **Watch-only** (Option A) | **Dedicated wallet** (Option B) |
|---|---|---|
| Server holds a spend key? | **No** — xpub only | Yes, but only for a throwaway hot wallet, in a `0600` file |
| Sweeping to cold storage | Manual (you, with your offline seed) | **Automatic** via the gas-tank sweeper |
| Your main wallet's seed on the server? | Never | Never (sweeper only *sends* to it) |
| Best for | Maximum safety | Hands-off operation |

Whichever you choose, your main/cold wallet's seed phrase stays offline and is never on
the server. See [`DEPLOYMENT.md`](DEPLOYMENT.md) for funding the gas tank and running in
production.
