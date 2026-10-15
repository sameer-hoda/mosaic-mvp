# Mosaic — WhatsApp Chief of Staff (MVP)

One product, three jobs: **ask** ("what's pending on me?"), **slash
commands** in any chat, and a **pulse** (3 daily briefs + rare real-time
pings). Replies are always drafts you approve with one word.

**Self-chat only.** The bot talks exclusively in your WhatsApp
self-chat ("Message yourself"). It never creates groups and never
messages anyone unless you confirm with `yes` (max 30 sends/day, dry-run
unless `MOSAIC_LIVE=1`).

## 5-minute promise (no phone needed)

Clone, install, and see a real morning brief built from the bundled
test snapshot — all read-only, zero sends, zero pairing:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r hermes_bot/requirements.txt \
            -r components/wa_slash_commands/requirements.txt
pip install pytest
python -m pytest tests/ -q
python scripts/morning_brief_dryrun.py
```

With a Gemini key you also get the full end-to-end pass (100 chats,
max 30 live calls, the rest on cheap rules):

```sh
GEMINI_API_KEY=<your-key> python scripts/e2e_dry_run.py
```

## What works

- **Scan** (`hermes_bot/scanner.py`): incremental cursor scan of recent
  group chats; snapshot opened read-only, state in `mosaic.db`.
- **Extract** (`extract.py` + `gemini.py`): banter/FYI/bot threads are
  rejected by cheap rules; only gated threads spend a Flash-Lite call
  (max 4 items per thread, verb-first `what`, fail-open to `[]`).
- **Brief** (`briefs.py` + `scheduler.py`): 09:00 / 13:30 / 18:30
  Asia/Kolkata, ranked direct-question > promised > owns > waiting-on,
  max 5 items + 2 "also moved"; `done N` / `later N` / `more N`,
  `quiet 22-8`, `pulse` on demand.
- **Draft** (`draft.py`): three grounded A/B/C options per brief item —
  any date/number not already in the thread is flagged, never invented.
- **Send** (`send.py`): `send NB` shows a confirm screen naming the
  exact chat + text; only `yes` within 5 minutes sends. Never a group,
  never a non-contact, never a chat you never wrote in.
- **Pings** (`pings.py`): one line + `draft N`, only when a message
  directly addresses you, you've been silent 20 min, the chat is
  unmuted, and it's outside quiet hours (max 6/day).
- **Pipeline cycle** (`cycle.py`, wired into `main.py` behind
  `MOSAIC_CYCLE=1`): one scan → extract → brief → draft pass per tick,
  dry-run unless `MOSAIC_LIVE=1`.

## Dry-run reference

| Command | What it does |
|---|---|
| `python -m pytest tests/ -q` | full suite, offline (live LLM test is opt-in) |
| `python scripts/dry_run_extract.py` | 20-chat scan: counts + per-chat items |
| `python scripts/dry_run_pings.py` | ping-rule decisions over 20 chats |
| `python scripts/morning_brief_dryrun.py [--owner Sam]` | one printed morning brief |
| `GEMINI_API_KEY=... python scripts/e2e_dry_run.py` | 100 chats, brief, ping eval, sample draft, tokens + cost |

Every script is print-only: the snapshot is never written to, the
throwaway `mosaic.db` lives in a temp dir, and `e2e_dry_run.py`
refuses to run with `MOSAIC_LIVE=1`. The key is read from env and is
never printed, logged, or committed (tests use fake keys).

## Going live (needs your phone)

1. `bash scripts/hermes_start.sh` (builds the Go bridge, starts
   bridge + brain), open the wizard URL it prints.
2. Set a setup password, paste your Gemini key (or export
   `GEMINI_API_KEY`), scan the WhatsApp QR.
3. Export the run config and restart:
   `MOSAIC_LIVE=1 OWNER_NAMES=Sam TZ=Asia/Kolkata MOSAIC_CYCLE=1`.
4. Railway: one container (`Dockerfile` → supervisord runs bridge +
   brain), mount a volume at `/data`, set template vars
   `GEMINI_API_KEY` and `TZ`. Healthcheck is `/health`; the QR setup
   flow runs at `/` on first boot. See `railway.toml`.

## What still needs live phone pairing

- QR pairing itself and the self-chat welcome message.
- Real scheduled briefs delivered to self-chat (`MOSAIC_CYCLE=1` tick).
- `yes`-confirmed sends to contacts (`MOSAIC_LIVE=1`).
- Owner-name resolution from the live session store (dry-run derives
  only the owner phone from message metadata).
- Precision is 13/20 keep right now (see `docs/PRECISION.md`) — the
  brief still carries ~1–2 junk items; the follow-ups listed there are
  open.

## Base

Forked from [mosaic-wa](https://github.com/sameer-hoda/mosaic-wa).
Port functions, not folders. See `docs/MVP-SCOPE.md` for the Week 1 /
Week 2 plan.
