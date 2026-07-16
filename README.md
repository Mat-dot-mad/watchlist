# Watchlist

A small self-hosted Flask dashboard for tracking a personal stock watchlist. Pulls quotes, analyst ratings, recommendation summaries, and 30-day price history from Yahoo Finance, stores everything in a local SQLite database, and refreshes once a day on a schedule.

Originally deployed on Railway, currently self-hosted on a Raspberry Pi reachable only over a private Tailscale network.

---

## What it does

- **Daily refresh** — once a day (configurable hour) the app fetches fresh data for every ticker in the database and saves a snapshot.
- **Dashboard** — a sortable table with current price, target price, upside %, analyst sentiment bar, 30-day sparkline, and key fundamentals.
- **Per-ticker detail page** — analyst upgrade/downgrade history, recommendation trend over the last 4 months, 1-year price chart, EPS estimates vs. actuals, and revenue/earnings history.
- **Add / remove tickers** through the UI; the list is mirrored to `tickers.txt` so it survives a database wipe.
- **Manual refresh** button for ad-hoc updates outside the schedule.
- **Login wall** — single shared password (`DASHBOARD_PASSWORD`). If unset, the login is skipped (only safe behind Tailscale or on localhost).

---

## Tech stack

- **Python 3** + **Flask** for the web app
- **gunicorn** as the WSGI server in production
- **APScheduler** for the daily in-process refresh job
- **yfinance** for Yahoo Finance data
- **SQLite** for storage (single file, no separate DB server)

---

## Project layout

```
.
├── app.py                  # Flask app factory, routes, scheduler, template filters
├── fetcher.py              # All Yahoo Finance fetching (single ticker + bulk + detail page)
├── db.py                   # SQLite schema, queries, ticker import/export helpers
├── requirements.txt
├── tickers.txt             # Plain list of ticker symbols, one per line
├── templates/              # Jinja2 templates (base, dashboard, ticker_detail, login)
└── static/                 # CSS + JS for the dashboard
```

---

## Environment variables

| Variable | Required? | Purpose |
|---|---|---|
| `PORT` | no (default `5000`) | Port gunicorn binds to. |
| `SECRET_KEY` | yes (production) | Flask session signing key. Generate with `python3 -c 'import secrets; print(secrets.token_hex(32))'`. |
| `DASHBOARD_PASSWORD` | recommended | Password for the `/login` page. If unset, login is bypassed — only safe on localhost or behind a VPN. |
| `DATABASE_PATH` | no (default `watchlist.db` next to `app.py`) | Where the SQLite file lives. In production, point this somewhere persistent like `/var/lib/watchlist/watchlist.db`. |
| `REFRESH_HOUR` | no (default `5`) | Hour of day (0–23, server local time) the auto-refresh runs. |
| `TICKERS_FILE` | no (default `tickers.txt`) | Where the app writes the ticker list when you add/remove tickers. In production, point it outside the git clone (e.g. `/var/lib/watchlist/tickers.txt`) — otherwise the app modifies a git-tracked file and the next `git pull` will refuse to run. The repo's `tickers.txt` is still used as a seed on a fresh, empty database. |

**Never commit any of these values to the repo.** Local development can use a `.env` file (already gitignored) or shell `export`s; production reads them from a service-managed env file.

---

## Running it locally (Mac)

```bash
# 1. Clone and enter the repo
git clone <this-repo-url>
cd watchlist

# 2. Create and activate a virtualenv
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set the minimum env vars and run
export SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
export DASHBOARD_PASSWORD=devpass    # or unset for no login during dev
python app.py
```

Then open http://localhost:5000.

The first run creates `watchlist.db` in the project directory and seeds tickers from `tickers.txt`.

To trigger a refresh manually without waiting for the daily schedule, click "Refresh" in the dashboard or hit `POST /refresh` while logged in.

---

## Production deployment (high level)

The current production target is a Raspberry Pi on the home LAN, reachable only over Tailscale.

The full step-by-step setup runbook lives outside this repo (in personal notes) — what follows is enough to remember the *shape* of the deployment.

### Layout on the Pi

```
/opt/watchlist/app/                 # this repo, cloned
/opt/watchlist/app/venv/            # Python virtualenv with requirements installed
/opt/watchlist/backup.sh            # daily SQLite snapshot + off-site copy
/var/lib/watchlist/watchlist.db     # the live database
/var/lib/watchlist/backups/         # local 7-day rotation of nightly snapshots
/etc/watchlist.env                  # env vars, mode 640, owned by root:watchlist
/etc/systemd/system/watchlist.service
```

### Why this shape

- **Code in `/opt`, data in `/var/lib`** — Linux convention. Lets backups target a single small directory and lets the systemd unit grant write access only to the data dir.
- **Dedicated `watchlist` system user** — the app runs as an unprivileged user with no login shell. If the app is ever compromised, the attacker doesn't get the personal user account.
- **systemd unit with hardening flags** (`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `ReadWritePaths` limited to the two app dirs, etc.) — defence in depth so a code-execution bug can't easily escape into the rest of the system.
- **Env file mode 640, owned by `root:watchlist`** — only root and the service can read it, so other Pi users can't see secrets.

### Network posture

- **UFW firewall**: default deny inbound, allow SSH, allow everything on `tailscale0`. The dashboard port is open on `0.0.0.0` *inside* the Pi but blocked on every interface except the Tailscale one — so it's only reachable from devices on the personal tailnet.
- **No public-internet exposure**. No port forwarding, no public TLS cert, no DNS pointing at a home IP. The dashboard URL only resolves and only responds for devices logged into the same Tailscale network.

### Operations

```bash
# Status and logs
sudo systemctl status watchlist
sudo journalctl -u watchlist -f
sudo journalctl -u watchlist --since "today"

# Lifecycle
sudo systemctl restart watchlist
sudo systemctl stop watchlist
sudo systemctl start watchlist

# Update to latest code
sudo -u watchlist -H bash
cd /opt/watchlist/app
git pull
source venv/bin/activate
pip install -r requirements.txt
exit
sudo systemctl restart watchlist
```

### Backups

- **Local snapshot**: a cron job runs `/opt/watchlist/backup.sh` once a day before the refresh hour. It uses SQLite's `.backup` command (safe against concurrent writes), keeps the last 7 daily files in `/var/lib/watchlist/backups/`.
- **Off-site copy**: the same script then pushes the backups directory to Google Drive via `rclone`, with a 30-day retention policy on the cloud side. The rclone config lives in `/root/.config/rclone/rclone.conf` (mode 600).

### Restoring from a backup

```bash
sudo systemctl stop watchlist
sudo cp /var/lib/watchlist/backups/watchlist-YYYY-MM-DD.db /var/lib/watchlist/watchlist.db
sudo chown watchlist:watchlist /var/lib/watchlist/watchlist.db
sudo systemctl start watchlist
```

If the local backups are gone too, pull a snapshot from Google Drive first:

```bash
rclone copy gdrive:watchlist-backups/watchlist-YYYY-MM-DD.db /tmp/
```

---

## Common gotchas

- **`yfinance` throttles**. If sparklines or recommendations come back empty for some tickers, it's almost always Yahoo rate-limiting. The fetcher retries 3 times with backoff for sparklines, but a single refresh can still miss a few — they recover on the next run.
- **`REFRESH_HOUR` is server local time**, not UTC. Set the Pi's timezone correctly, or just pick the hour in the timezone you actually want.
- **NaN in JSON**. The dashboard JS used to break when a numeric field came back as `NaN` because `JSON.parse` rejects it. Sparkline values are now coerced to floats before being saved, but if a future field is added that can be NaN, wrap it in `_fmt(...)` in `fetcher.py`.
- **Keep gunicorn at `--workers 1`.** The daily refresh scheduler (APScheduler) runs *inside* the app process. With more than one worker, each worker starts its own scheduler and the refresh runs multiple times concurrently, hammering Yahoo. If the app ever needs more workers, the scheduler must first move out of the web process (e.g. to a system cron job hitting a refresh endpoint).
- **`stock_data` history grows forever.** Every refresh inserts a fresh row per ticker (~14k rows/year at 38 tickers) and nothing prunes old rows. SQLite handles this fine for years, so it's deliberate for now — but if the DB or its backups ever feel bloated, a periodic `DELETE FROM stock_data WHERE updated_at < date('now', '-1 year')` is the fix.

---

## License

Personal project — no license declared.
