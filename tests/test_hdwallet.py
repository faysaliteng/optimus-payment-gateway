"""
Tests for optimus_gateway.hdwallet — the BIP32/44 derivation that lets the gateway
run WATCH-ONLY.

The single most important property proven here: an address the gateway derives from
a watch-only xpub (what it hands a payer) is exactly the address controlled by the
private key derived at the same index from the matching xprv (the spend key used to
sweep). If that ever drifted, funds would land at an address nobody can move.

Requires bip-utils + eth-account (already in requirements.txt). No network / no DB.
"""
from __future__ import annotations

import pytest

from optimus_gateway import hdwallet


@pytest.fixture(scope="module")
def wallet():
    """One fresh, isolated dedicated wallet for the whole module.

    generate_dedicated_wallet() returns {mnemonic, account_xprv, account_xpub,
    address_0} — the account_xpub is the WATCH-ONLY receiving key, account_xprv is
    the matching spend key.
    """
    w = hdwallet.generate_dedicated_wallet()
    # Shape check — these are the exact keys the rest of the gateway relies on.
    assert set(w) >= {"mnemonic", "account_xprv", "account_xpub", "address_0"}
    return w


def test_generated_xpub_is_watch_only_and_valid(wallet):
    """validate_xpub accepts the generated account_xpub and previews addresses 0 & 1."""
    res = hdwallet.validate_xpub(wallet["account_xpub"])
    assert res["ok"] is True
    assert res["address_0"].startswith("0x")
    assert res["address_1"].startswith("0x")
    # validate_xpub derives address_0 the same way generate_dedicated_wallet did.
    assert res["address_0"] == wallet["address_0"]


def test_validate_xpub_rejects_a_private_key(wallet):
    """A merchant must NOT be able to paste a spendable xprv where an xpub belongs —
    validate_xpub rejects it (so the secret never gets stored/logged)."""
    res = hdwallet.validate_xpub(wallet["account_xprv"])
    assert res["ok"] is False
    assert "error" in res
    assert "private" in res["error"].lower()


def test_validate_xpub_rejects_garbage():
    assert hdwallet.validate_xpub("").get("ok") is False
    assert hdwallet.validate_xpub("not-a-key").get("ok") is False


def test_address_from_xpub_is_deterministic(wallet):
    """The same (xpub, index) always yields the same address, and different indices
    yield different addresses (each order gets its own address)."""
    xpub = wallet["account_xpub"]
    for i in (1, 2, 5, 17):
        assert hdwallet.address_from_xpub(xpub, i) == hdwallet.address_from_xpub(xpub, i)
    addrs = {hdwallet.address_from_xpub(xpub, i) for i in range(1, 8)}
    assert len(addrs) == 7  # all distinct


def test_watchonly_address_matches_spend_key(wallet):
    """THE core invariant: the watch-only address at index i equals the address of
    the private key derived at index i from the xprv. Proven for several indices.

    (Compared case-insensitively: both libraries emit EIP-55 checksums, but the
    guarantee we care about is the same 20-byte address, not checksum formatting.)
    """
    xpub = wallet["account_xpub"]
    xprv = wallet["account_xprv"]
    for i in (0, 1, 2, 9, 100):
        watch_addr = hdwallet.address_from_xpub(xpub, i)
        priv = hdwallet.child_privkey(xprv, i)
        spend_addr = hdwallet.address_of_privkey(priv)
        assert watch_addr.lower() == spend_addr.lower(), f"mismatch at index {i}"


def test_index_zero_is_the_gas_tank(wallet):
    """Index 0 (the gas tank / main address) is derivable and matches the wallet's
    reported address_0 — per-order receiving addresses start at index 1."""
    assert (
        hdwallet.address_from_xpub(wallet["account_xpub"], 0).lower()
        == wallet["address_0"].lower()
    )
