"""
HD wallet — BIP32/44 derivation for per-order EVM addresses, xpub validation, and
the optional dedicated hot-wallet used for auto-sweeping.

Two modes, pick per deployment:

  WATCH-ONLY (safest)   You give the gateway only your account XPUB
                        (m/44'/60'/0'/0). It derives a fresh receiving address per
                        order (index 1,2,3,…) but holds NO private keys — it can
                        only WATCH. You sweep funds yourself with your offline seed.

  DEDICATED HOT WALLET  You generate a brand-new wallet just for the gateway
                        (generate_dedicated_wallet). Its account XPRV lives in a
                        0600 file (never DB/logs); index 0 is the gas tank; children
                        1,2,3,… are receiving addresses. The gateway can then
                        AUTO-SWEEP incoming funds to your cold main wallet. Your main
                        wallet's seed is never on the server.

Because every EVM chain shares the same secp256k1 address space, ONE derivation
serves BSC, Ethereum and Polygon — which is exactly why a buyer who pays on the
"wrong" EVM network still sends to an address you control (see sweeper.recover_wrongnet).

Derivation path: m/44'/60'/0'/0/index  (Bip44Coins.ETHEREUM, external chain).
Index 0 is reserved for the main wallet / gas tank; per-order addresses start at 1.
"""
from __future__ import annotations

import os


# --- xpub / xprv (watch-only or dedicated) ---------------------------------
def _bip44_node(extended_key: str):
    """Load a bip44 node from an account- or chain-level extended key. Accepts xpub
    (watch-only) or xprv. Tries the account level (…/0'/0 change) then falls back to
    a chain-level key (already at …/0)."""
    from bip_utils import Bip44, Bip44Coins
    return Bip44.FromExtendedKey(extended_key, Bip44Coins.ETHEREUM)


def address_from_xpub(xpub: str, index: int) -> str:
    """The receiving address at `index` under a watch-only account/chain xpub."""
    from bip_utils import Bip44Changes
    node = _bip44_node(xpub)
    try:
        leaf = node.Change(Bip44Changes.CHAIN_EXT).AddressIndex(int(index))
    except Exception:
        leaf = node.AddressIndex(int(index))
    return leaf.PublicKey().ToAddress()


def child_privkey(xprv: str, index: int) -> str:
    """Private key hex for child `index` of a dedicated-wallet xprv (account or
    chain level). Only used server-side to sign sweeps — never exposed."""
    from bip_utils import Bip44Changes
    node = _bip44_node(xprv)
    try:
        leaf = node.Change(Bip44Changes.CHAIN_EXT).AddressIndex(int(index))
    except Exception:
        leaf = node.AddressIndex(int(index))
    return leaf.PrivateKey().Raw().ToHex()


def address_of_privkey(priv: str) -> str:
    from eth_account import Account
    return Account.from_key(priv).address


def validate_xpub(xpub: str) -> dict:
    """Confirm a string is a usable WATCH-ONLY account xpub. Rejects private keys
    (xprv/yprv/zprv) so a merchant can't accidentally paste a spendable secret into
    a field that gets stored. Returns {ok, address_0?, error?}."""
    xpub = (xpub or "").strip()
    if not xpub:
        return {"ok": False, "error": "empty"}
    low = xpub.lower()
    if low.startswith(("xprv", "yprv", "zprv", "tprv")):
        return {"ok": False, "error": "that is a PRIVATE key — paste the xPUB (watch-only) instead"}
    if not low.startswith(("xpub", "ypub", "zpub", "tpub")):
        return {"ok": False, "error": "not an extended public key (expected xpub…)"}
    try:
        addr0 = address_from_xpub(xpub, 0)
        addr1 = address_from_xpub(xpub, 1)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "could not derive from this xpub (%s)" % str(exc)[:80]}
    return {"ok": True, "address_0": addr0, "address_1": addr1}


# --- dedicated wallet generation (one-time) --------------------------------
def generate_dedicated_wallet() -> dict:
    """Create a fresh, isolated BIP39 wallet for the gateway. Store account_xprv in a
    0600 file (the gateway's spend key for sweeping), publish account_xpub as the
    receiving key, and keep the mnemonic offline as a backup. address_0 is the gas
    tank. Returns {mnemonic, account_xprv, account_xpub, address_0}."""
    from bip_utils import (Bip39MnemonicGenerator, Bip39WordsNum, Bip39SeedGenerator,
                           Bip44, Bip44Coins, Bip44Changes)
    mnemonic = str(Bip39MnemonicGenerator().FromWordsNumber(Bip39WordsNum.WORDS_NUM_12))
    seed = Bip39SeedGenerator(mnemonic).Generate()
    chain = (Bip44.FromSeed(seed, Bip44Coins.ETHEREUM)
             .Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT))
    return {
        "mnemonic": mnemonic,
        "account_xprv": chain.PrivateKey().ToExtended(),
        "account_xpub": chain.PublicKey().ToExtended(),
        "address_0": chain.AddressIndex(0).PublicKey().ToAddress(),
    }


def load_sweep_xprv(path: str) -> str:
    """Read the dedicated hot-wallet xprv from its locked file. Returns '' if absent
    (which simply disables auto-sweep — deposits still credit)."""
    try:
        if not path or not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def save_sweep_xprv(path: str, xprv: str) -> None:
    """Write the xprv to a 0600 file, creating the directory locked-down."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xprv.strip())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
