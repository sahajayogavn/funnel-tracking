# Standalone server deployment (10.0.1.42)

<!-- Universal ID: doc:server-deploy-001 -->

The whole system (web dashboard, Chrome/CDP, inbox loop, HITL executor,
scheduler) runs on `10.0.1.42` from `/mnt/s2/sahajayogavn/funnel-tracking`,
under the `steve` account, with no root access and no dependency on the
operator's Mac. PostgreSQL is the `postgres-funnel-tracking` container on the
same host (see `deploy/postgres/`); `.env` / `web/.env.local` point
`DATABASE_URL` at `10.0.1.42:5432` because the container only binds that address.

## Setup / update

```bash
cd /mnt/s2/sahajayogavn/funnel-tracking
deploy/server/setup.sh            # venv + Chrome + web build + units
deploy/server/setup.sh --skip-chrome --skip-web   # units/deps only
```

User-level toolchain: `uv` + Python 3.13 and Node 24 live in `~/.local/bin`;
Chrome is unpacked into `~/.local/opt/google-chrome` by `install_chrome.sh`.
`requirements.lock.txt` pins the exact Python packages of the working venv.
Linger is enabled for `steve`, so enabled units start at boot.

## Services (`systemctl --user ...`)

| Unit | What | Log |
|---|---|---|
| `funnel-web` | Next.js dashboard on `:9995` | `logs/services/web.log` |
| `funnel-cdp-browser` | Chrome, CDP `:9222`, profile `data/chrome-profiles/Profile 4` | `logs/cdp_browser.log` |
| `funnel-inbox-loop` | `tools/run_inbox_mas_loop.sh pipeline` | `logs/services/inbox-loop.log` |
| `funnel-hitl-execution` | approved-action executor, draft-only | `logs/services/hitl-execution.log` |
| `funnel-scheduler` | `l5_scheduler.py --routes care --live` | `logs/services/scheduler.log` |

`funnel-inbox-loop` is not restarted after exit 75 (Facebook temporary block)
or 76 (fetch QA mismatch): root-cause those first.

## Facebook login (one time)

Chrome cookies from macOS are encrypted with the Mac Keychain and cannot be
decrypted on Linux, so the migrated profile starts logged out. Start
`funnel-cdp-browser`, connect to the server desktop with RustDesk, and log in
to Facebook in that Chrome window (the unit places it at `0,0`, on-screen). The profile
uses `--password-store=basic`, so the session stays readable by the service.

## Chrome sandbox (needs root once)

Without root, the unpacked Chrome cannot use its sandbox (the setuid
`chrome-sandbox` helper must be root-owned, and AppArmor blocks the
user-namespace fallback), so `start_cdp_browser.sh` falls back to
`--no-sandbox` with a warning in `logs/cdp_browser.log`. To restore the
sandbox, run once with sudo and restart the unit — the script detects it:

```bash
sudo chown root:root ~/.local/opt/google-chrome/chrome-sandbox
sudo chmod 4755 ~/.local/opt/google-chrome/chrome-sandbox
systemctl --user restart funnel-cdp-browser
```

Re-running `install_chrome.sh` (upgrade) replaces the helper, so repeat this.

Never run the fetch/MAS/HITL loops on the Mac and the server at the same time:
two browsers driving one Page inbox risks a Facebook block and duplicate drafts.
