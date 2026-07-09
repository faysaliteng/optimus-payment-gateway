# Production deployment

This guide takes the Optimus Payment Gateway from a cloned repo to a hardened,
internet-facing service with TLS, backups, and health monitoring. Pick **one** of the
two run methods (Docker *or* systemd) — the rest (nginx, backups, gas tank, monitoring)
applies to both.

Target: a fresh Ubuntu/Debian VPS. All commands are copy-pasteable; replace
`pay.yourdomain.com` and wallet addresses with your own.

**Prerequisites on the box:** a domain's A record pointing at the server, ports 80/443
open, and either Docker or Python 3.11+.

---

## 0. Get the code and configure

```bash
sudo mkdir -p /opt/optimus-gateway && sudo chown "$USER" /opt/optimus-gateway
git clone <this-repo> /opt/optimus-gateway
cd /opt/optimus-gateway
cp .env.example .env
```

Edit `.env` — at minimum set your receiving key, the public URL, and the merchant
secret (see [`XPUB_GUIDE.md`](XPUB_GUIDE.md) for the xpub):

```ini
OPG_BASE_URL=https://pay.yourdomain.com
OPG_GATEWAY_XPUB=xpub6C...              # watch-only; from XPUB_GUIDE.md
OPG_ENABLED_METHODS=usdt_bep20,usdt_polygon
OPG_MERCHANT_API_KEY=<public-key>
OPG_MERCHANT_API_SECRET=<long-random>  # e.g. `openssl rand -hex 32`
# Optional auto-sweep (Option B in XPUB_GUIDE.md):
# OPG_AUTO_SWEEP=true
# OPG_SWEEP_DESTINATION=0xYourColdMainWallet
```

Sanity-check the xpub before going further:

```bash
python run.py checkxpub "$OPG_GATEWAY_XPUB"    # or inside Docker, see below
```

> ⚠️ `.env` and `private/` contain secrets and are already in `.gitignore`. Lock them
> down: `chmod 600 .env`. Never commit either.

---

## 1A. Run method — Docker Compose (recommended)

The repo ships a [`Dockerfile`](../Dockerfile) and
[`docker-compose.yml`](../docker-compose.yml). The compose file mounts `./private`
(read-only) and `./data` (the SQLite DB) as volumes and adds a `/health` healthcheck.

```bash
cd /opt/optimus-gateway
mkdir -p data private            # data = sqlite db; private = sweep xprv (Option B only)

# Start ONLY the gateway service (the API + watcher + sweeper live in this one process):
docker compose up -d --build gateway

docker compose logs -f gateway   # watch it boot; Ctrl-C to detach
```

> **Note on the `admin` service:** `docker-compose.yml` also defines an optional `admin`
> service (`python -m admin.app`). The admin dashboard is not part of this core repo, so
> start the `gateway` service **by name** as shown above. Bringing up the whole file with
> a bare `docker compose up -d` will try (and fail) to start `admin` unless you've added
> that module.

Run one-off commands inside the container:

```bash
docker compose run --rm gateway python run.py checkxpub "$OPG_GATEWAY_XPUB"
docker compose run --rm gateway python run.py tanks        # gas-tank balances
docker compose exec gateway python run.py recover          # one-shot wrong-net recovery
```

Inside the container the paths are fixed by the Dockerfile:
`OPG_DB_PATH=/app/data/optimus_gateway.db` and
`OPG_SWEEP_KEY_PATH=/app/private/gateway_sweep/account.xprv`, which map to `./data` and
`./private` on the host.

Skip to [section 2 (nginx)](#2-nginx-reverse-proxy--tls).

---

## 1B. Run method — systemd (no Docker)

Use a dedicated, unprivileged user and a virtualenv.

```bash
sudo useradd --system --home /opt/optimus-gateway --shell /usr/sbin/nologin optimus || true
cd /opt/optimus-gateway
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
sudo mkdir -p data private
sudo chown -R optimus:optimus /opt/optimus-gateway
```

> **Important — the app does NOT auto-load `.env`.** Nothing in the code reads a `.env`
> file; Docker Compose injects it via `env_file`, but under systemd you must supply the
> variables yourself with `EnvironmentFile=`. **systemd does not strip inline comments**,
> so do not point `EnvironmentFile=` at the `.env.example`-style file with trailing
> `# comments` — it would fold the comment into the value. Keep a clean, comment-free env
> file for systemd:

```bash
sudo tee /etc/optimus-gateway.env >/dev/null <<'EOF'
OPG_BASE_URL=https://pay.yourdomain.com
OPG_DB_PATH=/opt/optimus-gateway/data/optimus_gateway.db
OPG_SWEEP_KEY_PATH=/opt/optimus-gateway/private/gateway_sweep/account.xprv
OPG_GATEWAY_XPUB=xpub6C...
OPG_ENABLED_METHODS=usdt_bep20,usdt_polygon
OPG_ACCEPT_USDC=true
OPG_MIN_CONFIRMATIONS=12
OPG_MERCHANT_API_KEY=your-public-key
OPG_MERCHANT_API_SECRET=your-long-random-secret
OPG_HOST=127.0.0.1
OPG_PORT=8000
EOF
sudo chmod 600 /etc/optimus-gateway.env
sudo chown optimus:optimus /etc/optimus-gateway.env
```

Create the unit:

```bash
sudo tee /etc/systemd/system/optimus-gateway.service >/dev/null <<'EOF'
[Unit]
Description=Optimus Payment Gateway (API + watcher + sweeper)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=optimus
Group=optimus
WorkingDirectory=/opt/optimus-gateway
EnvironmentFile=/etc/optimus-gateway.env
ExecStart=/opt/optimus-gateway/.venv/bin/python run.py serve
Restart=always
RestartSec=5

# hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/opt/optimus-gateway/data /opt/optimus-gateway/private

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now optimus-gateway
sudo systemctl status optimus-gateway
journalctl -u optimus-gateway -f          # live logs
```

Bind to `127.0.0.1` (as above) so only nginx can reach it directly.

---

## 2. nginx reverse proxy + TLS

Put nginx in front for TLS termination and a clean public URL. The gateway listens on
`127.0.0.1:8000`; nginx proxies `pay.yourdomain.com` to it.

```bash
sudo apt-get update && sudo apt-get install -y nginx
```

```bash
sudo tee /etc/nginx/sites-available/optimus-gateway >/dev/null <<'EOF'
server {
    listen 80;
    server_name pay.yourdomain.com;

    # (Certbot will add the 443 server block + redirect below.)

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
    }

    # Small QR/HTML checkout responses; allow modest bodies for webhooks you host elsewhere.
    client_max_body_size 1m;
}
EOF

sudo ln -sf /etc/nginx/sites-available/optimus-gateway /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Get a free Let's Encrypt certificate (Certbot rewrites the block above to add HTTPS on
443 and an HTTP→HTTPS redirect, and installs auto-renewal):

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d pay.yourdomain.com
sudo systemctl reload nginx
```

Verify end-to-end:

```bash
curl -s https://pay.yourdomain.com/health | python3 -m json.tool
```

Make sure `OPG_BASE_URL` in your env matches `https://pay.yourdomain.com` so the
`checkout_url` returned to merchants is correct.

---

## 3. Where the data + keys live (and how to back them up)

Two things on disk matter. Everything else is re-derivable from them.

| What | Docker path (host) | systemd path | Contains |
|---|---|---|---|
| **SQLite database** | `./data/optimus_gateway.db` (+ `-wal`, `-shm`) | `/opt/optimus-gateway/data/optimus_gateway.db` | orders, credited amounts, the anti-replay txid registry, HD address counter, webhook queue |
| **Sweep spend key** (Option B only) | `./private/gateway_sweep/account.xprv` (`0600`) | `/opt/optimus-gateway/private/gateway_sweep/account.xprv` | the dedicated hot-wallet `xprv` used to auto-sweep. **Watch-only (Option A) has no key here.** |

### Back up the database (WAL-safe)

The DB runs in WAL mode, so copy it with SQLite's online backup (never just `cp` a live
WAL database):

```bash
# systemd install:
sqlite3 /opt/optimus-gateway/data/optimus_gateway.db \
  ".backup '/opt/optimus-gateway/backups/opg-$(date +%F-%H%M).db'"

# Docker install:
docker compose exec gateway sh -c \
  "sqlite3 /app/data/optimus_gateway.db \".backup '/app/data/opg-$(date +%F-%H%M).db'\""
```

Automate it with cron (daily 03:15, keep 14 days):

```bash
( crontab -l 2>/dev/null; echo '15 3 * * * sqlite3 /opt/optimus-gateway/data/optimus_gateway.db ".backup /opt/optimus-gateway/backups/opg-$(date +\%F).db" && find /opt/optimus-gateway/backups -name "opg-*.db" -mtime +14 -delete' ) | crontab -
```

Ship the backups off-box (rsync/S3/rclone) — a local backup won't survive a dead disk.

### Back up the keys — do this OFFLINE, once

- **The real backup of your funds is the mnemonic**, not the `xprv` file. When you ran
  `python run.py newwallet` (Option B) it printed a 12-word phrase — that phrase
  regenerates the `account.xprv` and every receiving address. Write it on paper, store it
  offline, and you can lose the server without losing money.
- For watch-only (Option A), the server holds no key at all; your **offline seed** (kept
  off the server per [`XPUB_GUIDE.md`](XPUB_GUIDE.md)) is the only backup you need.
- Never put the mnemonic or `xprv` in your DB backups, git, or any cloud sync of the repo.

---

## 4. Fund the gas tank (auto-sweep only)

If `OPG_AUTO_SWEEP=true`, the sweeper pays gas from **index 0 of the dedicated wallet —
the same address on every EVM chain**. Each chain needs its **own native coin** in that
address (a BNB balance can't pay Ethereum gas).

Find the tank address and per-chain balances:

```bash
# systemd:
/opt/optimus-gateway/.venv/bin/python run.py tanks
# Docker:
docker compose run --rm gateway python run.py tanks
```

```
  usdt_bep20     0xAAA…  0.000000 BNB
  usdt_erc20     0xAAA…  0.000000 ETH
  usdt_polygon   0xAAA…  0.000000 POL
```

Send a small amount of the right native coin to that address on each enabled chain:

| Chain | Send | Suggested starter float |
|---|---|---|
| BSC (`usdt_bep20`) | **BNB** | ~0.02–0.05 BNB |
| Ethereum (`usdt_erc20`) | **ETH** | ~0.01–0.03 ETH (gas is pricey) |
| Polygon (`usdt_polygon`) | **POL** | ~2–5 POL |

The sweeper tops up each per-order address from this tank just-in-time before sweeping.
`OPG_GAS_ALERT_THRESHOLD` (default `0.005` native) is the low-balance line to watch; if a
tank runs dry the sweeper reports `gas_tank_low` and simply waits — **no deposit is
lost**, it just isn't forwarded until you refill. Re-run any missed sweeps + wrong-network
recoveries on demand:

```bash
python run.py recover        # credits (idempotent) + sweeps anything found
```

> Watch-only deployments (Option A) don't need a gas tank — you sweep manually with your
> offline seed and can ignore this section.

---

## 5. Health monitoring

The gateway exposes `GET /health` — a JSON snapshot of the (non-secret) config plus the
version. Use it for uptime checks and load-balancer probes.

```bash
curl -s https://pay.yourdomain.com/health
```

```json
{
  "ok": true,
  "version": "1.0.0",
  "config": {
    "base_url": "https://pay.yourdomain.com",
    "enabled_methods": ["usdt_bep20", "usdt_polygon"],
    "per_order_address_mode": true,
    "auto_sweep": false,
    "min_confirmations": 12,
    "...": "..."
  }
}
```

- **Docker** already ships a healthcheck in `docker-compose.yml` (hits `/health` every
  30s); `docker compose ps` shows `healthy`/`unhealthy`.
- **External uptime monitor** (UptimeRobot, Better Uptime, Healthchecks.io, …): point it
  at `https://pay.yourdomain.com/health` and alert on non-200 or `ok != true`.
- **systemd** restarts the process automatically (`Restart=always`). Tail logs with
  `journalctl -u optimus-gateway -f`; the watcher logs each tick that credits an order and
  the sweeper logs `credited/swept` counts.

Simple cron watchdog that alerts if `/health` is down (wire the `echo` to your alert of
choice):

```bash
( crontab -l 2>/dev/null; echo '*/5 * * * * curl -fsS https://pay.yourdomain.com/health >/dev/null || echo "optimus-gateway health check FAILED at $(date)" | logger -t optimus-gateway' ) | crontab -
```

Also worth watching over time: the `webhook_queue` table (rows stuck in `status=failed`
mean a merchant endpoint is rejecting callbacks) and gas-tank balances via
`python run.py tanks`.

---

## 6. Upgrade / rollback

```bash
cd /opt/optimus-gateway
# back up first (section 3), then:
git pull

# Docker:
docker compose up -d --build gateway

# systemd:
.venv/bin/pip install -r requirements.txt
sudo systemctl restart optimus-gateway
```

The database schema is created/migrated idempotently on startup (`init_db()` runs
`CREATE TABLE/INDEX IF NOT EXISTS`), so upgrades are safe. To roll back, `git checkout`
the previous tag and restart; restore a DB backup only if a release explicitly changed
schema in an incompatible way (none do as of v1.0.0).

---

## Deployment checklist

- [ ] `.env` filled in; `chmod 600 .env`; `OPG_MERCHANT_API_SECRET` is long + random.
- [ ] `python run.py checkxpub …` prints the expected `index 1` address.
- [ ] Gateway bound to `127.0.0.1`; only nginx is public.
- [ ] TLS via Certbot; `OPG_BASE_URL` uses `https://`.
- [ ] `/health` returns `ok: true` over HTTPS.
- [ ] DB backup cron in place and shipping off-box.
- [ ] Wallet **mnemonic** written down offline (Option B) / seed kept offline (Option A).
- [ ] Gas tanks funded per chain **if** `OPG_AUTO_SWEEP=true`.
- [ ] Uptime monitor watching `/health`.
- [ ] Tested a small real payment end-to-end before taking live traffic.
