# Hermes on Railway

The finance assistant runs as the Railway service `hermes` in the project `doeedd`
(environment `production`), next to the doeedd `api` and `Postgres` services.

```
Telegram ─► hermes (this image) ─► doeedd api ─► Postgres
               │  volume /opt/data          └► SumoPod (LLM)
               └► Google Drive (receipts)
```

## Files

| File | Role |
|---|---|
| `Dockerfile` | `nousresearch/hermes-agent` pinned by digest, plus tesseract, tzdata and this repo at `/opt/finance-agent` |
| `cont-init.sh` | Boot hook (`/etc/cont-init.d/50-finance-agent`): copies the cron script into `/opt/data/scripts`, links the fallback paths |
| `doeedd_notify.py` | Cron script for "doeedd daily check" (stdlib only) |

All Hermes state lives on the volume at `/opt/data` (`HERMES_HOME`): `config.yaml`, memory,
sessions, cron jobs, `doeedd/` (aliases, outbox, notify state) and `google_token.json`. Code
never lives on the volume: a change ships by redeploying.

## Railway service settings

| Setting | Value |
|---|---|
| Source | GitHub `T0MYAMMM/finance-agent`, branch `main` |
| Builder | Dockerfile, path `deploy/hermes/Dockerfile` |
| Start command | empty (the image runs `gateway run`) |
| Volume | 1 GB mounted at `/opt/data` |
| Public networking | none (Telegram uses long polling) |
| Restart policy | on failure, 5 retries |
| Region | same as `api` (Southeast Asia) |

Variables (values only in Railway, never in git):

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_ALLOWED_USERS` | the owner's Telegram user id |
| `TELEGRAM_HOME_CHANNEL` | the owner's chat id (cron delivery target) |
| `SUMOPOD_API_KEY` | SumoPod AI key |
| `DOEEDD_BASE_URL` | `https://<api host>/api/v1` |
| `DOEEDD_TOKEN` | `pnpm cli create-token --name hermes-railway` in the `api` container |
| `DOEEDD_TIMEZONE` | `Asia/Jakarta` |
| `DOEEDD_DRIVE_ROOT_FOLDER_ID` | Drive id of the "Personal Finance" folder |

## One-time setup on the volume

Run inside the container (`railway ssh --service hermes`). Settings are written with
`hermes config set` so the file keeps Hermes's own schema.

```bash
hermes config set model.provider custom
hermes config set model.base_url https://ai.sumopod.com/v1
hermes config set model.api_key '${SUMOPOD_API_KEY}'
hermes config set model.default deepseek-flash
hermes config set skills.external_dirs '["/opt/finance-agent/skills"]'
hermes config set terminal.env_passthrough \
  '["DOEEDD_BASE_URL","DOEEDD_TOKEN","DOEEDD_TIMEZONE","DOEEDD_DRIVE_ROOT_FOLDER_ID"]'
# Cheaper side tasks (titles, compression) on the same endpoint:
hermes config set auxiliary.title_generation.model glm-5.3-flash
hermes config set auxiliary.compression.model glm-5.3-flash

hermes cron create "0 19 * * *" --no-agent --script doeedd_notify.py \
  --deliver telegram --name "doeedd daily check"
```

Google Drive (receipts): run the google-workspace skill's `setup.py` once and paste the
authorisation code; the token is saved as `/opt/data/google_token.json`.

## Checks after a deploy

```bash
hermes status
hermes cron list                                  # next run shows 19:00 WIB
"$HERMES_PYTHON" /opt/finance-agent/doeedd.py --json health
"$HERMES_PYTHON" /opt/finance-agent/doeedd.py notify --dry-run
```

From Telegram: "kopi 25rb gopay", then "undo".
