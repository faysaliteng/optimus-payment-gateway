# Contributing to Optimus Payment Gateway

Thanks for helping make self-hosted, non-custodial crypto payments better for everyone! 🙌

## Ways to contribute
- ⭐ **Star the repo** and tell someone who needs it — genuinely the most helpful thing.
- 🐛 **Report bugs** — open an issue with steps to reproduce.
- 💡 **Suggest features** — a new chain, a wallet guide, a language example.
- 🌍 **Add integration examples** (Discord bot, WooCommerce, Shopify webhook, Go/PHP/Node clients).
- 🔗 **Add a chain** — see [`docs/CHAINS.md`](docs/CHAINS.md) (add a `CHAINS` entry + a `GAS` entry in `sweeper.py`).
- 📖 **Improve the docs** — especially the beginner path.

## Dev setup
```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt pytest
python -m pytest -q                                # all tests must pass
```

## Ground rules (this is money software)
- **Money math is integer cents** — never introduce floats into the ledger.
- **Never break idempotency** — a txid is burned before crediting; keep it that way.
- **Never log, store, or return private keys / xprv / seed phrases.** Watch-only by default.
- Keep the core **dependency-light** (SQLite + stdlib where possible).
- Add/adjust tests for anything you change. Hermetic tests only (no network in CI).

## Pull requests
1. Fork, branch from `main` (`git checkout -b feat/my-thing`).
2. Make focused changes + tests. Run `pytest` and `python -m compileall optimus_gateway server admin`.
3. Open a PR describing **what** and **why**. Small, reviewable PRs merge fastest.
4. By contributing you agree your work is MIT-licensed (see [LICENSE](LICENSE)).

## Security
Please **don't** open a public issue for vulnerabilities — see [SECURITY.md](SECURITY.md).
