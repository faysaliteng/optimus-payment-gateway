"""
Litecoin (LTC) — a native-coin UTXO gateway, the UTXO sibling of the EVM stablecoin
addressed gateway. Everything Litecoin lives here; the EVM watcher/sweeper never touch it.

Model
-----
  * ADDRESSES (watch-only). Per-order native-segwit (bech32 ``ltc1…``, cheapest fees)
    addresses are derived from a WATCH-ONLY account extended public key (BIP84 zpub/xpub).
    The server can generate addresses but CANNOT spend — deposit detection needs no private
    key. Wallets disagree on the version bytes AND the export depth (Electrum-LTC exports a
    depth-1 segwit key using Bitcoin's zpub version; a BIP84 hardware wallet exports depth-3
    with Litecoin's version), so ``_extkey_to_node`` normalizes the 4 version bytes to the
    standard xpub version before deriving — the key material is unchanged.

  * WATCHER (litecoinspace.org). A mempool.space-style API for Litecoin — free, no API key.
    We read confirmed received sats, per-(txid, vout) outputs, spendable UTXOs, and the
    recommended fee rate. The crediting caller burns each ``(txid, vout)`` once (idempotent).

  * PRICING (USD value). LTC is volatile, so a deposit is CREDITED at its USD value rather
    than matched 1:1 like a stablecoin. The USD/LTC rate is pluggable: register a provider
    with ``set_ltc_rate_provider`` (e.g. a CoinGecko/Binance ticker), pass an explicit
    ``rate=`` per call, or fall back to the ``ltc_usd_rate`` setting / ``OPG_LTC_USD_RATE``.
    Amounts are litoshis internally and become integer USD cents only at credit time
    (``sats_to_usd_cents``), matching the repo's integer-cents money convention.

  * SWEEP (optional, BIP143 P2WPKH signer). Litecoin uses Bitcoin's transaction format +
    BIP143 segwit sighash verbatim (only the address HRP + network differ), so the signer
    below is validated byte-for-byte against the official BIP143 P2WPKH test vector. Spend
    authority is the account extended PRIVATE key (zprv/xprv) — the private twin of the
    watch-only zpub, normalized identically — loaded from a locked 0600 file / env, NEVER the
    DB, NEVER logged. ``sweep_ltc`` consolidates confirmed UTXOs into ONE transaction to a
    cold destination, paying the fee out of the swept LTC (no gas tank — a UTXO chain funds
    its own fee).

Nothing here runs until the gateway is enabled and an xpub is configured, so importing this
module is inert and safe.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import struct
import urllib.parse
import urllib.request
from typing import Callable

from .chains import CHAINS

log = logging.getLogger("optimus_gateway.ltc")

_CFG = CHAINS["ltc"]
LTCSPACE_API = _CFG["api"]          # https://litecoinspace.org/api
LTC_HRP = _CFG["hrp"]               # "ltc"
LTC_SATS = 10 ** int(_CFG["decimals"])  # 1 LTC = 1e8 litoshis
DUST_LIMIT_SATS = 294               # P2WPKH dust floor

_TRUE = {"1", "true", "yes", "on", "y"}

# Sweep key location. The account xprv/zprv lives in a locked file (chmod 600) or an env
# var — NEVER the DB/settings and NEVER logged (mirrors the EVM sweep key policy).
LTC_SWEEP_KEY_PATH = os.getenv("OPG_LTC_SWEEP_KEY_PATH", "private/gateway_sweep/ltc_account.xprv")


def _http_get(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "OptimusGateway/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    import json
    return json.loads(body) if body.strip() else None


# --------------------------------------------------------------------------- config
def _setting(db_key: str, env_key: str, default: str = "") -> str:
    """Hot setting: DB first (admin can change it with no restart), then env var, then
    default. The DB read is lazy + best-effort so importing this module never needs a DB."""
    v = None
    try:
        from . import db
        v = db.get_setting(db_key, None)
    except Exception:  # noqa: BLE001
        v = None
    if v not in (None, ""):
        return str(v).strip()
    return (os.getenv(env_key, default) or "").strip()


def ltc_gateway_enabled() -> bool:
    return _setting("ltc_gateway_enabled", "OPG_LTC_ENABLED", "false").lower() in _TRUE


def ltc_gateway_xpub() -> str:
    """Account extended PUBLIC key (BIP84 zpub / BIP44 xpub) for per-order addresses."""
    return _setting("ltc_gateway_xpub", "OPG_LTC_XPUB")


def ltc_sweep_destination() -> str:
    return _setting("ltc_sweep_destination", "OPG_LTC_SWEEP_DESTINATION")


def ltc_sweep_min_usd() -> float:
    try:
        return float(_setting("ltc_sweep_min_usd", "OPG_LTC_SWEEP_MIN_USD", "0") or 0)
    except (TypeError, ValueError):
        return 0.0


def load_ltc_sweep_xprv(path: str = None) -> str:
    """Read the account xprv/zprv from its locked file. '' if absent (which simply disables
    auto-sweep — deposits still credit). NEVER read from the DB; NEVER logged."""
    path = path or LTC_SWEEP_KEY_PATH
    try:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
    except OSError:
        pass
    # env fallback for containerized/secret-manager deployments
    return (os.getenv("OPG_LTC_SWEEP_XPRV", "") or "").strip()


def save_ltc_sweep_xprv(xprv: str, path: str = None) -> None:
    """Persist the account xprv to a 0600 file, creating the directory locked-down."""
    path = path or LTC_SWEEP_KEY_PATH
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as fh:
        fh.write((xprv or "").strip())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ltc_auto_sweep_enabled() -> bool:
    """Auto-sweep runs only when explicitly enabled AND a signing key is present."""
    return (_setting("ltc_auto_sweep_enabled", "OPG_LTC_AUTO_SWEEP", "false").lower() in _TRUE
            and bool(load_ltc_sweep_xprv()))


# ------------------------------------------------ address derivation (watch-only)
def _extkey_to_node(ext_key: str):
    """Parse an account extended PUBLIC key into a generic BIP32 node, VERSION-AGNOSTICALLY.
    Wallets disagree on the version bytes AND depth: Electrum-LTC exports a segwit key at
    depth 1 (its own m/0' derivation) using BITCOIN's zpub version (0x04b24746); a BIP84
    hardware wallet exports depth 3 (m/84'/2'/0') with Litecoin's version. We normalize the
    4 version bytes to the standard xpub version so bip_utils will parse ANY of them — the
    key material (chain code + pubkey) is unchanged — then derive change/index RELATIVE to
    whatever account node it is. The address type is fixed by us (native segwit ltc1…)."""
    from bip_utils import Base58Decoder, Base58Encoder, Bip32Slip10Secp256k1
    payload = Base58Decoder.CheckDecode((ext_key or "").strip())  # ver(4)+depth+fp+child+cc(32)+key(33)
    std = bytes.fromhex("0488b21e") + payload[4:]
    return Bip32Slip10Secp256k1.FromExtendedKey(Base58Encoder.CheckEncode(std))


def derive_ltc_address(xpub: str, index: int, change: int = 0) -> str:
    """One native-segwit (ltc1…) address at receive-chain `index` from a watch-only account
    xpub. No private key involved — the server cannot spend these."""
    from bip_utils import P2WPKHAddrEncoder
    node = _extkey_to_node(xpub)
    pk = node.DerivePath("%d/%d" % (int(change), int(index))).PublicKey().RawCompressed().ToBytes()
    return P2WPKHAddrEncoder.EncodeKey(pk, hrp=LTC_HRP)


def validate_ltc_xpub(xpub: str) -> dict:
    """Sanity-check an account xpub by deriving address 0. Returns {ok, sample?/error?}."""
    xpub = (xpub or "").strip()
    if not xpub:
        return {"ok": False, "error": "empty"}
    if xpub.lower().startswith(("xprv", "yprv", "zprv", "tprv", "ltpv")):
        return {"ok": False, "error": "that is a PRIVATE key — paste the xPUB/zpub (watch-only) instead"}
    try:
        a0 = derive_ltc_address(xpub, 0)
        a1 = derive_ltc_address(xpub, 1)
        if not str(a0).startswith(LTC_HRP + "1"):
            return {"ok": False, "error": "derived a non-segwit address — expected a BIP84 zpub/xpub"}
        return {"ok": True, "sample": a0, "address_0": a0, "address_1": a1}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


# ------------------------------------------------------ watcher (litecoinspace)
def ltc_address_received_sats(addr: str) -> int:
    """Total CONFIRMED litoshis ever received by an address (funded_txo_sum)."""
    d = _http_get(f"{LTCSPACE_API}/address/{urllib.parse.quote(addr)}") or {}
    return int((d.get("chain_stats") or {}).get("funded_txo_sum") or 0)


def ltc_address_incoming(addr: str) -> list[dict]:
    """Every output PAYING this address, per (txid, vout): [{txid, vout, value_sats,
    confirmed, block_height}]. The credit path burns each (txid, vout) once (idempotent)."""
    txs = _http_get(f"{LTCSPACE_API}/address/{urllib.parse.quote(addr)}/txs") or []
    out = []
    for t in txs:
        txid = t.get("txid")
        status = t.get("status") or {}
        for i, vout in enumerate(t.get("vout") or []):
            if (vout.get("scriptpubkey_address") or "") == addr:
                out.append({
                    "txid": txid, "vout": i, "value_sats": int(vout.get("value") or 0),
                    "confirmed": bool(status.get("confirmed")),
                    "block_height": status.get("block_height"),
                })
    return out


def ltc_address_utxos(addr: str) -> list[dict]:
    """Spendable UTXOs at an address (for the sweep): [{txid, vout, value(sats), status}]."""
    return _http_get(f"{LTCSPACE_API}/address/{urllib.parse.quote(addr)}/utxo") or []


def ltc_fee_rate_sat_vb() -> float:
    """Recommended fee (sat/vByte). Litecoin fees are tiny; fall back to 2 sat/vB."""
    try:
        d = _http_get(f"{LTCSPACE_API}/v1/fees/recommended", timeout=10) or {}
        return max(1.0, float(d.get("halfHourFee") or d.get("hourFee") or 2))
    except Exception:  # noqa: BLE001
        return 2.0


# --------------------------------------------------------------------------- pricing
# LTC is volatile, so a deposit credits at its USD VALUE. The rate is pluggable: register a
# provider once (e.g. a CoinGecko/Binance ticker), pass rate= per call, or fall back to a
# stored `ltc_usd_rate` setting / OPG_LTC_USD_RATE env var.
_rate_provider: Callable[[], float] | None = None


def set_ltc_rate_provider(fn: Callable[[], float] | None) -> None:
    """Register a callable returning the current USD price of 1 LTC (float). Pass None to
    clear it. This is how the host app wires its own price feed into the gateway."""
    global _rate_provider
    _rate_provider = fn


def ltc_usd_rate(rate: float = None) -> float:
    """Current USD price of 1 LTC. Precedence: explicit `rate` arg > registered provider >
    `ltc_usd_rate` setting / OPG_LTC_USD_RATE. Returns 0.0 if none is available (callers
    treat a 0 rate as 'cannot price' and credit nothing)."""
    if rate is not None:
        try:
            return float(rate) if float(rate) > 0 else 0.0
        except (TypeError, ValueError):
            return 0.0
    if _rate_provider is not None:
        try:
            r = float(_rate_provider() or 0)
            if r > 0:
                return r
        except Exception:  # noqa: BLE001
            log.debug("ltc rate provider failed", exc_info=True)
    try:
        return float(_setting("ltc_usd_rate", "OPG_LTC_USD_RATE", "0") or 0)
    except (TypeError, ValueError):
        return 0.0


def usd_to_ltc(usd: float, rate: float = None) -> float:
    """Quote: how much LTC a buyer must send for a given USD order value (0 if no rate)."""
    r = ltc_usd_rate(rate)
    return round(float(usd) / r, 8) if r > 0 else 0.0


def sats_to_usd(sats: int, rate: float = None) -> float:
    """Credit value: what a received amount of litoshis is worth in USD right now."""
    r = ltc_usd_rate(rate)
    return round((int(sats) / LTC_SATS) * r, 2) if r > 0 else 0.0


def sats_to_usd_cents(sats: int, rate: float = None) -> int:
    """Credit value in whole USD cents (what the wallet is credited). Single rounding — the
    watcher passes this straight to the address-credit path as the arrived amount, keeping
    all money math in the repo's integer-cents convention."""
    r = ltc_usd_rate(rate)
    if r <= 0:
        return 0
    return int(round((int(sats) / LTC_SATS) * r * 100))


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTO-SWEEP SIGNER — P2WPKH, BIP143 native segwit
#
#  Litecoin uses Bitcoin's transaction format + BIP143 segwit sighash verbatim (only the
#  address HRP + network differ), so the signer below is validated byte-for-byte against the
#  official BIP143 P2WPKH test vector before it ever signs real coins.
#
#  Spend authority comes from the account extended PRIVATE key (zprv/xprv) — the private twin
#  of the watch-only zpub. It is normalized the SAME way as the zpub (version bytes →
#  standard, derive change/index relative to the account node), so the derived keys control
#  exactly the addresses the deposit watcher credits. The key is loaded from a locked file /
#  env (never the DB), and only ever signs sweeps of in-transit deposit funds to a cold dest.
# ═══════════════════════════════════════════════════════════════════════════════
def _dsha256(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def _hash160(b: bytes) -> bytes:
    return hashlib.new("ripemd160", hashlib.sha256(b).digest()).digest()


def _varint(n: int) -> bytes:
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def _extkey_to_priv_node(ext_key: str):
    """Parse an account extended PRIVATE key (zprv/xprv/ltpv…) into a generic BIP32 node,
    version-agnostically — the private-key twin of _extkey_to_node. Normalizes the 4 version
    bytes to the standard xprv version (0x0488ade4) so bip_utils parses ANY wallet's export
    (Electrum depth-1 m/0', BIP84 depth-3, etc.); the key material is unchanged."""
    from bip_utils import Base58Decoder, Base58Encoder, Bip32Slip10Secp256k1
    payload = Base58Decoder.CheckDecode((ext_key or "").strip())  # ver(4)+depth+fp+child+cc(32)+key(33)
    std = bytes.fromhex("0488ade4") + payload[4:]
    return Bip32Slip10Secp256k1.FromExtendedKey(Base58Encoder.CheckEncode(std))


def derive_ltc_keypair(xprv: str, index: int, change: int = 0):
    """(priv32, pub33) at receive-chain `index` from the account xprv/zprv. The pubkey's
    P2WPKH address equals derive_ltc_address(xpub, index) for the matching xpub."""
    node = _extkey_to_priv_node(xprv).DerivePath("%d/%d" % (int(change), int(index)))
    priv = node.PrivateKey().Raw().ToBytes()
    pub = node.PublicKey().RawCompressed().ToBytes()
    return priv, pub


def _addr_to_script_pubkey(addr: str) -> bytes:
    """scriptPubKey for a destination address. Supports native-segwit v0 P2WPKH (ltc1q…,
    the common cold-wallet case)."""
    from bip_utils import P2WPKHAddrDecoder
    wp = P2WPKHAddrDecoder.DecodeAddr((addr or "").strip(), hrp=LTC_HRP)
    if len(wp) != 20:
        raise ValueError("unsupported destination address (need a native-segwit ltc1q… P2WPKH)")
    return b"\x00\x14" + wp  # OP_0 <20-byte-program>


def _p2wpkh_sighash(inputs, out_serialized: bytes, idx: int, version: int,
                    locktime: int, sighash_type: int) -> bytes:
    """BIP143 sighash for P2WPKH input `idx`. inputs: [{txid, vout, value, pub, sequence}]."""
    prevouts = b""
    sequences = b""
    for i in inputs:
        prevouts += bytes.fromhex(i["txid"])[::-1] + struct.pack("<I", int(i["vout"]))
        sequences += struct.pack("<I", int(i["sequence"]))
    hash_prevouts = _dsha256(prevouts)
    hash_sequence = _dsha256(sequences)
    hash_outputs = _dsha256(out_serialized)
    inp = inputs[idx]
    keyhash = _hash160(inp["pub"])
    script_code = b"\x19\x76\xa9\x14" + keyhash + b"\x88\xac"  # len(0x19) DUP HASH160 <20> EQUALVERIFY CHECKSIG
    outpoint = bytes.fromhex(inp["txid"])[::-1] + struct.pack("<I", int(inp["vout"]))
    preimage = (
        struct.pack("<I", version)
        + hash_prevouts + hash_sequence
        + outpoint
        + script_code
        + struct.pack("<Q", int(inp["value"]))
        + struct.pack("<I", int(inp["sequence"]))
        + hash_outputs
        + struct.pack("<I", locktime)
        + struct.pack("<I", sighash_type)
    )
    return _dsha256(preimage)


def build_signed_p2wpkh_tx(inputs, outputs, *, version: int = 2, locktime: int = 0,
                           sighash_type: int = 1):
    """Build a fully-signed native-segwit transaction spending P2WPKH `inputs` to `outputs`.
      inputs:  [{txid, vout, value(sats), priv(32b), pub(33b), sequence?}]
      outputs: [(script_pubkey_bytes, amount_sats), ...]
    Returns (raw_tx_hex, txid_hex). ECDSA is RFC6979-deterministic + low-S (libsecp256k1)."""
    import coincurve
    for i in inputs:
        i.setdefault("sequence", 0xFFFFFFFF)
    out_ser = b""
    for spk, amt in outputs:
        out_ser += struct.pack("<Q", int(amt)) + _varint(len(spk)) + spk

    witnesses = []
    for idx, i in enumerate(inputs):
        sighash = _p2wpkh_sighash(inputs, out_ser, idx, version, locktime, sighash_type)
        sig = coincurve.PrivateKey(i["priv"]).sign(sighash, hasher=None)  # DER, low-S
        witnesses.append([sig + bytes([sighash_type]), i["pub"]])

    # Non-witness serialization (for txid) and witness serialization (for broadcast).
    base = struct.pack("<I", version) + _varint(len(inputs))
    for i in inputs:
        base += bytes.fromhex(i["txid"])[::-1] + struct.pack("<I", int(i["vout"])) + b"\x00" + struct.pack("<I", int(i["sequence"]))
    base += _varint(len(outputs)) + out_ser + struct.pack("<I", locktime)
    txid = _dsha256(base)[::-1].hex()

    wtx = struct.pack("<I", version) + b"\x00\x01" + _varint(len(inputs))
    for i in inputs:
        wtx += bytes.fromhex(i["txid"])[::-1] + struct.pack("<I", int(i["vout"])) + b"\x00" + struct.pack("<I", int(i["sequence"]))
    wtx += _varint(len(outputs)) + out_ser
    for w in witnesses:
        wtx += _varint(len(w))
        for item in w:
            wtx += _varint(len(item)) + item
    wtx += struct.pack("<I", locktime)
    return wtx.hex(), txid


# ------------------------------------------------------- sweep orchestration
def ltc_broadcast_tx(raw_hex: str) -> str:
    """Broadcast a raw tx via litecoinspace (mempool-style POST /tx). Returns the 64-hex
    txid on success; raises on an HTTP error OR any 200 whose body is NOT a txid (so the
    caller never marks a deposit swept on a broadcast that didn't actually land)."""
    req = urllib.request.Request(
        f"{LTCSPACE_API}/tx", data=(raw_hex or "").strip().encode(),
        headers={"Content-Type": "text/plain", "User-Agent": "OptimusGateway/1.0"},
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        body = r.read().decode("utf-8", "replace").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", body):
        raise ValueError(f"broadcast returned no txid (body: {body[:150]})")
    return body.lower()


def _estimate_vsize(n_in: int, n_out: int) -> int:
    """Approx vsize of an all-P2WPKH tx: ~68 vB/input, ~31 vB/output, ~11 overhead."""
    return int(11 + n_in * 68 + n_out * 31)


def verify_sweep_key(xprv: str, xpub: str = None, sample_indices=(0, 1, 2, 5)) -> dict:
    """Confirm the account xprv/zprv derives the SAME addresses as the watch-only xpub — i.e.
    it really controls the deposit addresses. `xpub` defaults to the configured gateway xpub.
    Returns {ok, sample?/error?}."""
    xpub = (xpub or ltc_gateway_xpub() or "").strip()
    if not xpub:
        return {"ok": False, "error": "no account xpub is configured to check the private key against"}
    try:
        from bip_utils import P2WPKHAddrEncoder
        for i in sample_indices:
            want = derive_ltc_address(xpub, i)
            _, pub = derive_ltc_keypair(xprv, i)
            got = P2WPKHAddrEncoder.EncodeKey(pub, hrp=LTC_HRP)
            if got != want:
                return {"ok": False, "error": f"private key does not match your wallet (address #{i} differs)"}
        return {"ok": True, "sample": derive_ltc_address(xpub, 0)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


def sweep_ltc(candidates, *, dest: str = None, xprv: str = None, force: bool = False,
              min_usd: float = None, rate: float = None, broadcast: bool = True) -> dict:
    """Consolidate every CONFIRMED balance on the given LTC per-order addresses into ONE
    signed transaction to the cold destination (fee paid in LTC out of the swept amount — no
    gas tank; a UTXO chain funds its own fee).

    `candidates` is an iterable of the per-order addresses to sweep, each a mapping with at
    least ``pay_address`` and ``address_index`` (the same row shape the EVM sweeper consumes
    from the DB). The caller decides which addresses are in scope — freshly-funded, a
    re-check window for a dropped/late sweep, etc. — keeping this function decoupled from any
    particular DB schema. Candidate selection is validated against LIVE on-chain UTXOs: an
    address already swept returns no UTXOs (skipped); a dropped sweep or a fresh deposit
    reappears as a live UTXO and gets (re-)swept.

    Watch-only-safe: does nothing unless a valid sweep xprv (from `xprv`, else the locked
    key file/env) that MATCHES the deposit xpub is configured. The returned
    ``swept_deposit_ids`` lets the caller mark those deposits swept once broadcast succeeds.
    Returns {status, txid?, inputs?, sent?, usd?, swept_deposit_ids?, ...}."""
    if not ltc_gateway_enabled():
        return {"status": "disabled"}
    xprv = (xprv or load_ltc_sweep_xprv() or "").strip()
    if not xprv:
        return {"status": "no_key"}
    v = verify_sweep_key(xprv)
    if not v.get("ok"):
        return {"status": "key_mismatch", "error": v.get("error")}
    dest = (dest or ltc_sweep_destination() or "").strip()
    if not dest:
        return {"status": "no_destination"}
    try:
        dest_spk = _addr_to_script_pubkey(dest)
    except Exception as exc:  # noqa: BLE001
        return {"status": "bad_destination", "error": str(exc)[:150]}

    from bip_utils import P2WPKHAddrEncoder
    inputs = []
    swept_deposit_ids = []
    total_in = 0
    for r in (candidates or []):
        addr = str(r.get("pay_address") or "")
        idx = r.get("address_index")
        if not addr or idx is None:
            continue
        try:
            priv, pub = derive_ltc_keypair(xprv, int(idx))
        except Exception:  # noqa: BLE001
            continue
        # Never sign for an address we can't reproduce from the key.
        if P2WPKHAddrEncoder.EncodeKey(pub, hrp=LTC_HRP) != addr:
            continue
        try:
            utxos = ltc_address_utxos(addr)
        except Exception:  # noqa: BLE001
            continue
        got_utxo = False
        for u in utxos:
            if not (u.get("status") or {}).get("confirmed"):
                continue
            val = int(u.get("value") or 0)
            if val <= 0:
                continue
            inputs.append({"txid": u["txid"], "vout": int(u["vout"]), "value": val,
                           "priv": priv, "pub": pub})
            total_in += val
            got_utxo = True
        if got_utxo and r.get("id") is not None:
            swept_deposit_ids.append(r["id"])

    if not inputs:
        return {"status": "nothing_to_sweep"}

    usd_value = sats_to_usd(total_in, rate)
    min_usd = ltc_sweep_min_usd() if min_usd is None else float(min_usd)
    if not force and min_usd > 0 and usd_value < min_usd:
        return {"status": "below_threshold", "usd": usd_value, "min_usd": min_usd,
                "inputs": len(inputs)}

    fee = int(_estimate_vsize(len(inputs), 1) * ltc_fee_rate_sat_vb()) + 1
    send_amount = total_in - fee
    if send_amount <= DUST_LIMIT_SATS:
        return {"status": "dust", "total_in": total_in, "fee": fee}
    try:
        raw_hex, local_txid = build_signed_p2wpkh_tx(inputs, [(dest_spk, send_amount)])
    except Exception as exc:  # noqa: BLE001
        return {"status": "sign_failed", "error": str(exc)[:200]}

    if not broadcast:
        return {"status": "unbroadcast", "raw_hex": raw_hex, "txid": local_txid,
                "inputs": len(inputs), "total_in": total_in, "fee": fee,
                "sent": send_amount, "usd": sats_to_usd(send_amount, rate), "dest": dest,
                "swept_deposit_ids": swept_deposit_ids}
    try:
        txid = ltc_broadcast_tx(raw_hex) or local_txid
    except Exception as exc:  # noqa: BLE001
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]  # HTTPError body
        except Exception:  # noqa: BLE001
            detail = str(exc)[:200]
        return {"status": "broadcast_failed", "error": detail, "raw_hex": raw_hex}

    return {
        "status": "ok", "txid": txid, "inputs": len(inputs),
        "addresses": len(swept_deposit_ids), "total_in": total_in, "fee": fee,
        "sent": send_amount, "usd": sats_to_usd(send_amount, rate), "dest": dest,
        "swept_deposit_ids": swept_deposit_ids,
    }
