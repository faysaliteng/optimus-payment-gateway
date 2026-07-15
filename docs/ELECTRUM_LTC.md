# Electrum-LTC — complete setup & maintenance guide

This is the start-to-finish walkthrough for running the Litecoin gateway from an
**Electrum-LTC** wallet: installing it safely, creating a dedicated wallet, exporting the
**watch-only account key (`zpub`)** the gateway needs, optionally exporting the **account
private key (`zprv`)** for hands-off auto-sweeping, and keeping the whole thing healthy
over time.

Read [`LITECOIN.md`](LITECOIN.md) first for *what* the LTC gateway does and *why*. This
document is the *how* for one specific (and recommended) wallet.

> **You never paste a seed phrase into the gateway.** The gateway needs the account
> **public** key (`zpub`) to watch for deposits, and — only if you opt into Phase-2
> auto-sweep — a dedicated account **private** key (`zprv`) in a locked file. Your 12-word
> Electrum seed stays on paper, offline, forever. See [`SECURITY.md`](SECURITY.md).

---

## 0. The end state (a map of what you're building)

By the end you will have:

| Thing | Where it lives | Secret? |
|---|---|---|
| 12-word Electrum seed | on **paper, offline** — the only backup of received funds | 🔴 **YES** — never typed online |
| Account **`zpub`** (watch-only) | gateway setting `ltc_gateway_xpub` / env `OPG_LTC_XPUB` | 🟢 no — safe to store |
| Account **`zprv`** *(Phase-2 only)* | a `0600` file, path in `OPG_LTC_SWEEP_KEY_PATH` | 🔴 YES — hot key, bounded blast radius |
| Cold **`ltc1q…`** sweep destination | gateway setting `ltc_sweep_destination` | 🟢 no — it's just an address |

Phase 1 (watch-only) uses only the top two rows and the destination. Phase 2 (auto-sweep)
adds the `zprv`. You can ship Phase 1 today and turn on Phase 2 later without redoing anything.

---

## 1. Install Electrum-LTC — and verify it's genuine

Fake, malware-laced "Electrum" builds are one of the oldest coin-stealing tricks. Do these
two things and you're safe:

### 1.1 Download only from the official site

**https://electrum-ltc.org** — nowhere else. Not an app store, not a search-ad link, not a
"mirror". Bookmark it.

### 1.2 Verify the signature (5 minutes, worth it)

The site publishes, next to each download, a `.asc` **signature file** and the developer's
**PGP public key**. Verifying proves the file you downloaded is the one the developer
actually built.

```bash
# 1. Import the signing key published on electrum-ltc.org (the site lists its ID/fingerprint).
gpg --import electrum-ltc-signing-key.asc

# 2. Verify the installer against its .asc signature:
gpg --verify electrum-ltc-4.x.x.x.exe.asc  electrum-ltc-4.x.x.x.exe
```

A good result says **`Good signature from "…"`** with the name shown on the official site.
Ignore the yellow *"not certified with a trusted signature"* warning — that only means you
haven't personally signed the developer's key; the signature itself is still valid. If you
see **`BAD signature`**, delete the file and start over from the official site.

> Windows users without GPG: install [Gpg4win](https://gpg4win.org). macOS: `brew install
> gnupg`. Linux: `gpg` is almost always already present.

### 1.3 Install / run

Windows: run the verified `.exe`. macOS: open the `.dmg`. Linux: `pip install` the AppImage
or the published package per the site's instructions. First launch asks you to pick a data
directory — the default is fine.

---

## 2. Create a dedicated gateway wallet

Use a **brand-new wallet that exists only for the gateway**, so its addresses and seed are
never mixed with personal funds.

1. **File → New/Restore.** Name it something obvious, e.g. `optimus-ltc-gateway`.
2. Choose **Standard wallet → Next.**
3. Choose **Create a new seed → Next.**
4. Seed type: **Segwit → Next.** *(This is what produces native `ltc1…` addresses — the
   only type the gateway derives and sweeps. Do not pick "Legacy".)*
5. Electrum shows a **12-word seed**. **Write it on paper.** Make two copies, store them in
   two separate physical places. Do **not** photograph it, type it into any website, or
   save it to a file, cloud drive, or password manager that syncs. Click **Next**, re-enter
   the words to confirm.
6. **Set a strong password.** This encrypts the wallet file *and the private key at rest* on
   disk. Remember it — you'll need it in Phase 2 (§6), and Electrum cannot recover it.

You now have a working, receive-capable Litecoin wallet whose seed only you hold.

> **Electrum seeds are NOT BIP39.** Electrum uses its own seed format. That means you
> **cannot** paste an Electrum seed into the offline BIP39 tool from
> [`XPUB_GUIDE.md`](XPUB_GUIDE.md), and you don't need to — you'll export the keys directly
> from Electrum below. (The BIP39 tool is for BIP39 wallets like Trust Wallet / hardware
> wallets.)

---

## 3. Export the account public key (`zpub`) → give it to the gateway

This is the watch-only key the gateway uses to generate a fresh deposit address per order
and watch the chain for payments. It **cannot spend** anything.

1. In Electrum-LTC: **Wallet → Information.**
2. Copy the **Master Public Key** — a long string starting `zpub…` (or `Zpub…`).
3. Hand it to the gateway (setting **or** env — pick one):

   ```bash
   # As a DB setting:
   python -c "from optimus_gateway import db; db.set_setting('ltc_gateway_xpub', 'zpub6...')"
   ```
   ```ini
   # …or in .env:
   OPG_LTC_XPUB=zpub6...
   ```

### Why the "wrong" version bytes don't matter (the gotcha, handled for you)

Electrum's Master Public Key has two quirks a naïve parser would choke on:

1. it is a **depth-1** key (Electrum's own `m/0'` account node), not the depth-3
   `m/84'/2'/0'` a hardware wallet exports; and
2. it carries **Bitcoin `zpub` version bytes** (`0x04b24746`), not a Litecoin-specific prefix.

The gateway's parser normalises only the 4 **version bytes** to the standard `xpub` version
and derives `change/index` **relative to whatever account node it's given**. The key
material (chain code + public key) is never altered. Result: whatever Electrum hands you and
a "textbook" BIP84 Litecoin account key both parse and both produce the **same `ltc1…`
addresses**. **You paste exactly what Electrum shows you — it just works.**

---

## 4. Verify before you trust it

Confirm the gateway derives the *same* addresses your wallet does — a 60-second check that
rules out a copy-paste slip or the wrong wallet.

```bash
# Validate + show address 0 (must be an ltc1… you recognise):
python -c "from optimus_gateway import ltc; print(ltc.validate_ltc_xpub('zpub6...'))"

# Show the first few per-order addresses the gateway will hand out:
python -c "from optimus_gateway import ltc; print([ltc.derive_ltc_address('zpub6...', i) for i in range(4)])"
```

Now cross-check in Electrum: **View → Show Addresses**, open the **Addresses** tab, and
compare the first receive addresses to what the commands printed. **Index 0 must match
Electrum's first receive address.** If they match, the gateway is watching addresses your
seed — and only your seed — controls.

If `validate_ltc_xpub` reports it's not an `ltc1…` / segwit key, you probably created a
*Legacy* wallet in §2.4 — recreate it as **Segwit**.

---

## 5. Go live watch-only (Phase 1 — recommended)

Point the gateway at a cold destination and enable the method. No spending key is on the
server.

```bash
# Cold wallet you'll sweep to (address only — its seed is NEVER on the server):
python -c "from optimus_gateway import db; db.set_setting('ltc_sweep_destination', 'ltc1q...')"
```
```ini
# Enable LTC (add it to your methods):
OPG_ENABLED_METHODS=usdt_bep20,ltc
OPG_LTC_ENABLED=true
```

Buyers now get a fresh `ltc1…` per order; deposits credit at the live LTC/USD rate
(see [`LITECOIN.md`](LITECOIN.md) §4). **You collect the funds yourself, manually** (§7.3),
using this same Electrum wallet — because it holds the seed, it already controls every
address the gateway generated. This is a complete, safe deployment; Phase 2 is optional.

---

## 6. (Optional) Phase-2 auto-sweep — export the account private key (`zprv`)

Only do this if you want the gateway to **automatically** forward collected deposits to your
cold wallet. It puts a **dedicated hot key** on the server (never your personal seed), whose
blast radius is bounded to *un-swept deposit balances* ([`LITECOIN.md`](LITECOIN.md) §7).

### 6.1 Get the account `zprv` out of Electrum — the right way

1. **View → Show Console** (a Python console tab appears at the bottom).
2. Run **exactly** this, with *your wallet password*:

   ```python
   wallet.keystore.get_master_private_key('YOUR_WALLET_PASSWORD')
   ```

   It returns a `zprv…` — the private twin of the `zpub` from §3.

> ### 🔴 The gotcha that costs people an hour
> Do **not** read `wallet.keystore.xprv` directly. On a **password-protected** wallet that
> attribute is the **encrypted** blob (base64 with `+`, `/`, `=` characters) — pasting it
> anywhere as a key will silently fail validation. The **only** correct export is
> `get_master_private_key('password')`, which *decrypts* it for you. If what you copied
> contains `+`/`/`/`=` and doesn't start with a clean `zprv`, you grabbed the encrypted blob.

### 6.2 Store it in a locked file (never the DB, never a plain env value)

```bash
python -c "from optimus_gateway import ltc; ltc.save_ltc_sweep_xprv('zprv...')"   # writes a 0600 file
```
```ini
# Tell the gateway where the locked key file is, and turn on sweeping:
OPG_LTC_SWEEP_KEY_PATH=private/gateway_sweep/ltc_account.xprv
OPG_LTC_AUTO_SWEEP=true
OPG_LTC_SWEEP_MIN_USD=5            # hold small balances until they're worth a sweep
```

### 6.3 Prove the hot key matches the watch-only key

```bash
python -c "from optimus_gateway import ltc; print(ltc.verify_sweep_key('zprv...', 'zpub...'))"
```

`verify_sweep_key` derives addresses `0/1/2/5` from the private key and asserts they equal
the ones the `zpub` produces. **If it doesn't match, stop** — you exported the `zprv` from a
different wallet/account than the `zpub` the watcher is using, and a sweep would sign for
addresses the watcher never credited. Re-export both from the **same** wallet.

### 6.4 Test with one small forced sweep

Send a few cents of LTC to a live order, let it confirm, then trigger one sweep and confirm
it lands at your cold address before you rely on it. The signer builds a BIP143 P2WPKH
transaction, deducts the tiny miner fee **from the swept amount itself** (there is no gas
tank), and broadcasts via litecoinspace ([`LITECOIN.md`](LITECOIN.md) §5).

---

## 7. Maintain it over time

### 7.1 Keep Electrum-LTC updated

Update only from **electrum-ltc.org**, and **re-verify the signature** (§1.2) every time.
An auto-update popup that links anywhere else is a red flag.

### 7.2 Watch balances without exposing keys (the recommended monitoring setup)

Keep the **seed wallet offline/cold**, and monitor deposits from an online machine with a
**watch-only companion**:

1. On the online machine, Electrum-LTC → **File → New/Restore → Standard wallet → Use a
   master key.**
2. Paste the **`zpub`** from §3 (the *public* key). Finish.

This wallet shows every gateway address and its balance in real time but **cannot spend** —
perfect for an always-on dashboard. Your spend-capable seed wallet only comes out (offline)
when you sweep.

### 7.3 Manually sweep to cold (Phase 1)

Because the gateway derives from your seed, your Electrum wallet already **holds** every
per-order deposit. Consolidating them to cold storage is one ordinary send:

1. Open the seed wallet (offline machine ideally). **Send** tab.
2. **Pay to:** your cold `ltc1q…` destination.
3. Click **Max** to send the entire balance, set a normal fee, **Send**.

That single transaction sweeps all order addresses at once for a few hundredths of a cent.

### 7.4 Back it up

- **The 12-word seed** is the master backup — two paper copies, two locations. With it you
  can restore the entire wallet on any Electrum-LTC, anywhere, even if the app/computer dies.
- **The wallet file** (optional convenience copy) lives at:
  - Windows: `%APPDATA%\Electrum-LTC\wallets\`
  - macOS: `~/.electrum-ltc/wallets/`
  - Linux: `~/.electrum-ltc/wallets/`
  Keep it password-protected. The seed alone is enough to restore, but the file preserves
  labels/history.

### 7.5 Rotate if a key is ever exposed

If the Phase-2 `zprv` file or (worse) the seed is ever exposed: create a **new** Segwit
wallet (§2), sweep all funds from the old wallet into the new one, repoint the gateway to
the new `zpub`/`zprv`, and retire the old wallet. The bounded blast radius (un-swept float)
is why keeping sweeps frequent / the `OPG_LTC_SWEEP_MIN_USD` low limits the damage window.

---

## 8. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| The `zprv` I copied has `+` `/` `=` in it and won't validate | You read `keystore.xprv` (the **encrypted** blob). Use `wallet.keystore.get_master_private_key('password')` instead (§6.1). |
| `verify_sweep_key` says the keys don't match | The `zprv` and `zpub` are from **different** wallets/accounts. Re-export **both** from the same Electrum wallet (§3 and §6.1). |
| `validate_ltc_xpub` says it's not a segwit/`ltc1` key | You created a **Legacy** wallet. Recreate it as **Segwit** (§2.4) and re-export the `zpub`. |
| Address 0 doesn't match Electrum's first address | Wrong wallet's key pasted, or a typo — re-copy the Master Public Key (§3) and re-run the §4 check. |
| I tried to paste my Electrum seed into the BIP39 tool and it's invalid | Electrum seeds aren't BIP39. Don't use the BIP39 tool for Electrum — export the `zpub`/`zprv` directly (§3, §6). |
| Balance looks lower than deposits | Unconfirmed deposits aren't credited/spendable yet. The watcher only counts **confirmed** outputs; wait for a block. |
| A manual sweep is "stuck" / low fee | Bump the fee in Electrum (right-click the tx → *Increase fee*), or resend at the litecoinspace-recommended sat/vByte. |
| "not enough funds" sweeping a fresh deposit | It's still unconfirmed, or below the dust floor (294 sats). Wait for confirmation. |

---

## See also

- [`LITECOIN.md`](LITECOIN.md) — the LTC gateway design, pricing, sweep model, rollout checklist.
- [`XPUB_GUIDE.md`](XPUB_GUIDE.md) — getting an **EVM** xpub (and a **BIP39** wallet's LTC `zpub`) with the offline tool; the safety rules.
- [`SECURITY.md`](SECURITY.md) — key model and threat model for watch-only vs. hot keys.
