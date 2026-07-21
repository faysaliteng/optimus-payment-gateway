#!/usr/bin/env python3
"""
Manual Binance check — verify a customer-submitted Binance Pay order id (or an
on-chain deposit txid) against YOUR real Binance history, from the command line.

Reads OPG_BINANCE_API_KEY / OPG_BINANCE_API_SECRET (read-only key) and the optional
OPG_BINANCE_PAY_ID / OPG_BINANCE_AMOUNT_TOLERANCE / OPG_BINANCE_MIN_AGE_MINUTES.

Examples:
    # verify a Pay order id paid you 4.00 USDT
    python examples/binance_manual_check.py 443746280424488960 --amount 4.00

    # search deeper (up to ~18 months of history)
    python examples/binance_manual_check.py 443746280424488960 --amount 4.00 --deep

    # verify an on-chain deposit txid credited your Binance account
    python examples/binance_manual_check.py 0xabc...def --amount 20 --txid

    # verify AND burn the reference (anti-replay) so it can't be reused to credit again
    python examples/binance_manual_check.py 443746280424488960 --amount 4.00 --claim

Exit code 0 = verified, 1 = not verified, 2 = misconfigured. Read-only unless --claim.
"""
import argparse
import json
import os
import sys

# Runnable straight from a checkout (no `pip install -e .` needed): put the repo root
# on the path so `optimus_gateway` imports whether or not the package is installed.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimus_gateway.binance import BinanceAccount, BinanceVerifier


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify a Binance Pay order id / deposit txid.")
    ap.add_argument("reference", help="Binance Pay order id (digits) or, with --txid, a deposit txid")
    ap.add_argument("--amount", type=float, required=True, help="expected amount")
    ap.add_argument("--currency", default="USDT", help="expected currency (default USDT)")
    ap.add_argument("--deep", action="store_true", help="page back through ~18 months of history")
    ap.add_argument("--txid", action="store_true", help="reference is an on-chain deposit txid")
    ap.add_argument("--claim", action="store_true",
                    help="on success, also BURN the reference in the registry (anti-replay)")
    args = ap.parse_args()

    acc = BinanceAccount.from_config()
    if not acc.enabled():
        print("Set OPG_BINANCE_API_KEY and OPG_BINANCE_API_SECRET (read-only) first.", file=sys.stderr)
        return 2

    verifier = BinanceVerifier(acc)
    conn = verifier.test_connection()
    if not conn.get("ok"):
        print("Binance connection FAILED: %s" % conn.get("message"), file=sys.stderr)
        return 2

    if args.txid:
        result = verifier.verify_deposit_txid(args.reference, args.amount)
    elif args.claim:
        result = verifier.verify_and_claim(
            args.reference, args.amount, expected_currency=args.currency, deep=args.deep)
    else:
        result = verifier.verify_pay_reference(
            args.reference, args.amount, expected_currency=args.currency, deep=args.deep)

    # never dump the full raw tx (may contain counterparty PII) unless asked
    printable = {k: v for k, v in result.items() if k != "raw"}
    print(json.dumps(printable, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
