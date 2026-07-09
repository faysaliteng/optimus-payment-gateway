"""
Fake-token protection.

The gateway must ONLY ever scan / credit / sweep REAL stablecoin token contracts. A
scammer can deploy a token they NAME "USDT" / "USDC" / "BSC-USD" at some other contract
and send it to a gateway address — it must never be treated as a real payment. These
tests pin the allowlist (built from the CHAINS registry) and the is_real_stablecoin guard.
"""
import re

from optimus_gateway.chains import (
    CHAINS,
    EVM_METHODS,
    REAL_STABLECOIN_CONTRACTS,
    TOKEN_BY_CONTRACT,
    is_real_stablecoin,
    stablecoins_for_chain,
)

_ADDR_RE = re.compile(r"^0x[0-9a-f]{40}$")

# Spot-check that the canonical real contracts are present + correct (a wrong address
# here would silently stop crediting a whole chain).
_KNOWN_REAL = {
    "bsc_usdt": "0x55d398326f99059ff775485246999027b3197955",
    "bsc_usdc": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
    "eth_usdt": "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "eth_usdc": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "polygon_usdt": "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
    "polygon_usdc": "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
    "arbitrum_usdc": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
    "optimism_usdt": "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58",
    "base_usdc": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
    "avalanche_usdt": "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7",
}

# Real-world SCAM tokens that impersonate stablecoins by NAME at bogus contracts — must
# be rejected. (These were actually observed hitting a production gateway address.)
_KNOWN_FAKE = [
    "0xe33bea8c034bb4591d5c8f981af5b78ed56080ae",  # scam named "BSC-USD"
    "0x966b5e26fec3cffba03628197df170501d1728b6",  # scam named "USDT"
    "0x532abd96be22c4080bc4e3e78523cbb83023d7ef",  # scam token
    "0x0000000000000000000000000000000000000000",
    "0x1234567890123456789012345678901234567890",
    "",
    None,
    "not-an-address",
]


def test_registry_contracts_are_well_formed():
    for method in EVM_METHODS:
        for sym, contract in CHAINS[method]["tokens"].items():
            assert _ADDR_RE.match(contract.lower()), f"{method}/{sym} malformed: {contract}"


def test_whitelist_covers_every_registry_token():
    """Every token contract in the registry is on the allowlist and passes the guard."""
    assert REAL_STABLECOIN_CONTRACTS, "allowlist must not be empty"
    for method in EVM_METHODS:
        for sym, contract in CHAINS[method]["tokens"].items():
            assert contract.lower() in REAL_STABLECOIN_CONTRACTS, f"{method}/{sym} not allowlisted"
            assert is_real_stablecoin(contract), f"{method}/{sym} rejected by guard"


def test_known_real_contracts_present_and_correct():
    for name, contract in _KNOWN_REAL.items():
        assert is_real_stablecoin(contract), f"{name} {contract} should be REAL"
        # case-insensitive (mixed-case checksummed input still matches)
        assert is_real_stablecoin("0x" + contract[2:].upper()), f"{name} mixed-case failed"


def test_fake_tokens_are_rejected():
    for fake in _KNOWN_FAKE:
        assert not is_real_stablecoin(fake), f"{fake!r} MUST NOT be treated as real"
        if fake and _ADDR_RE.match(str(fake).lower()):
            assert str(fake).lower() not in REAL_STABLECOIN_CONTRACTS


def test_reverse_lookup_maps_back_to_chain():
    for method in EVM_METHODS:
        for sym, contract in CHAINS[method]["tokens"].items():
            info = TOKEN_BY_CONTRACT[contract.lower()]
            assert info["symbol"] == sym
            assert info["method"] == method
            assert info["chain_id"] == CHAINS[method]["chain_id"]


def test_every_chain_has_usdt_and_usdc():
    """Each EVM chain the gateway supports must offer at least USDT and a USDC form."""
    for method in EVM_METHODS:
        toks = stablecoins_for_chain(method)
        assert "USDT" in toks, f"{method} missing USDT"
        assert any(s.startswith("USDC") or s in ("USDbC",) for s in toks), f"{method} missing USDC"
