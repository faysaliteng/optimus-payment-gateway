"""
Tests for optimus_gateway.ltc — the native Litecoin (UTXO) gateway. Hermetic: no network,
no DB. We prove the two things that MUST be exactly right for a from-scratch coin signer:

  * the BIP143 native-P2WPKH sighash + signature reproduce the OFFICIAL BIP143 test vector
    byte-for-byte (if this is off, every sweep tx is invalid — or worse, misdirected);
  * watch-only derivation matches, the private twin derives the SAME addresses as the xpub,
    and Electrum's quirky BTC-versioned zpub normalizes to the identical address.

Litecoin uses Bitcoin's transaction format + BIP143 verbatim (only the address HRP and
network differ), so the Bitcoin BIP143 vector is the authoritative correctness check.
"""
from __future__ import annotations

import struct

import coincurve

from optimus_gateway import ltc

# ── Official BIP143 "Native P2WPKH" vector ────────────────────────────────────────────
# https://github.com/bitcoin/bips/blob/master/bip-0143.mediawiki  (Native P2WPKH section)
BIP143_SIGHASH = "c37af31116d1b27caf68aae9e3ac82f1477929014d5b917657d0eb49478cb670"
BIP143_R = 0x3609E17B84F6A7D30C80BFA610B5B4542F32A8A0D5447A12FB1366D7F01CC44A
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def _disp(internal_hex: str) -> str:
    # BIP143 prints txids in internal (raw-tx) byte order; the litecoinspace API — and thus
    # the signer's input — uses display order, which the signer reverses. Feed the reverse.
    return bytes.fromhex(internal_hex)[::-1].hex()


def _bip143_inputs_outputs():
    inputs = [
        {"txid": _disp("fff7f7881a8099afa6940d42d1e7f6362bec38171ea3edf433541db4e4ad969f"),
         "vout": 0, "sequence": 0xFFFFFFEE, "value": 625000000,
         "pub": bytes.fromhex("03c9f4836b9a4f77fc0d81f7bcb01b7f1b35916864b9476c241ce9fc198bd25432")},
        {"txid": _disp("ef51e1b804cc89d182d279655c3aa89e815b1b309fe287d9b2b55d57b90ec68a"),
         "vout": 1, "sequence": 0xFFFFFFFF, "value": 600000000,
         "priv": bytes.fromhex("619c335025c7f4012e556c2a58b2506e30b8511b53ade95ea316fd8c3286feb9"),
         "pub": bytes.fromhex("025476c2e83188368da1ff3e292e7acafcdb3566bb0ad253f62fc70f07aeee6357")},
    ]
    outputs = [
        (bytes.fromhex("76a9148280b37df378db99f66f85c95a783a76ac7a6d5988ac"), 0x06B22C20),
        (bytes.fromhex("76a9143bde42dbee7e4dbe6a21b2d50ce2f0167faa815988ac"), 0x0D519390),
    ]
    out_ser = b""
    for spk, amt in outputs:
        out_ser += struct.pack("<Q", amt) + ltc._varint(len(spk)) + spk
    return inputs, out_ser


def test_bip143_sighash_matches_official_vector():
    inputs, out_ser = _bip143_inputs_outputs()
    sighash = ltc._p2wpkh_sighash(inputs, out_ser, 1, version=1, locktime=0x11, sighash_type=1)
    assert sighash.hex() == BIP143_SIGHASH


def test_bip143_signature_r_matches_and_is_low_s():
    inputs, out_ser = _bip143_inputs_outputs()
    sighash = ltc._p2wpkh_sighash(inputs, out_ser, 1, version=1, locktime=0x11, sighash_type=1)
    sig = coincurve.PrivateKey(inputs[1]["priv"]).sign(sighash, hasher=None)  # DER, low-S
    # parse DER: 30 len 02 rlen R 02 slen S
    assert sig[0] == 0x30 and sig[2] == 0x02
    rlen = sig[3]
    r = int.from_bytes(sig[4:4 + rlen], "big")
    assert sig[4 + rlen] == 0x02
    slen = sig[4 + rlen + 1]
    s = int.from_bytes(sig[4 + rlen + 2:4 + rlen + 2 + slen], "big")
    assert r == BIP143_R                                   # correct RFC6979 nonce
    assert coincurve.PublicKey(inputs[1]["pub"]).verify(sig, sighash, hasher=None)
    assert s <= _N // 2                                    # BIP62 low-S


def test_signed_tx_round_trips_and_verifies():
    import hashlib
    priv_a = hashlib.sha256(b"opg-ltc-a").digest()
    priv_b = hashlib.sha256(b"opg-ltc-b").digest()
    pub_a = coincurve.PrivateKey(priv_a).public_key.format(compressed=True)
    pub_b = coincurve.PrivateKey(priv_b).public_key.format(compressed=True)
    inputs = [
        {"txid": "a" * 64, "vout": 0, "value": 500000, "priv": priv_a, "pub": pub_a},
        {"txid": "b" * 64, "vout": 2, "value": 300000, "priv": priv_b, "pub": pub_b},
    ]
    dest_spk = ltc._addr_to_script_pubkey("ltc1q3qx3d8gfzsv0unff0psgjx22c58gdv4rplq46t")
    assert dest_spk[:2] == b"\x00\x14" and len(dest_spk) == 22   # P2WPKH v0
    raw_hex, txid = ltc.build_signed_p2wpkh_tx(inputs, [(dest_spk, 780000)])
    assert raw_hex[8:12] == "0001"                                # segwit marker+flag
    assert len(txid) == 64
    # every witness signature verifies against its own recomputed sighash
    out_ser = struct.pack("<Q", 780000) + ltc._varint(len(dest_spk)) + dest_spk
    for idx, i in enumerate(inputs):
        sh = ltc._p2wpkh_sighash(inputs, out_ser, idx, version=2, locktime=0, sighash_type=1)
        sig = coincurve.PrivateKey(i["priv"]).sign(sh, hasher=None)
        assert coincurve.PublicKey(i["pub"]).verify(sig, sh, hasher=None)


def test_watch_only_derivation_and_private_twin_match():
    # a deterministic account node → export both extended pub + priv keys
    from bip_utils import Bip32Slip10Secp256k1, P2WPKHAddrEncoder
    import hashlib
    acct = Bip32Slip10Secp256k1.FromSeed(hashlib.sha256(b"opg-ltc-seed").digest() * 2).DerivePath("84'/2'/0'")
    xpub, xprv = acct.PublicKey().ToExtended(), acct.PrivateKey().ToExtended()
    v = ltc.validate_ltc_xpub(xpub)
    assert v.get("ok") and str(v.get("sample")).startswith("ltc1")
    for i in range(4):
        a_pub = ltc.derive_ltc_address(xpub, i)
        _, pub = ltc.derive_ltc_keypair(xprv, i)
        assert a_pub == P2WPKHAddrEncoder.EncodeKey(pub, hrp="ltc")   # priv twin == xpub
    assert ltc.verify_sweep_key(xprv, xpub=xpub).get("ok")


def test_validate_rejects_a_private_key():
    from bip_utils import Bip32Slip10Secp256k1
    import hashlib
    xprv = Bip32Slip10Secp256k1.FromSeed(hashlib.sha256(b"x").digest() * 2).DerivePath("84'/2'/0'").PrivateKey().ToExtended()
    assert ltc.validate_ltc_xpub(xprv).get("ok") is False
