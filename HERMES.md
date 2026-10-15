# Mosaic (formerly Hermes) — Project Overview & Build Log

> **Date:** 2026-08-08
> **Status:** Working prototype. Slash commands + MeChat assistant verified end-to-end.
> Bridge runs on port **8877** (8080/8081 are used by local dev servers on this machine).
> MeChat = owner's **"My notes" group** (`<owner_phone>-1633800754@g.us`), configurable via `MECHAT_JID`.

---

## What Was Built

Hermes is a unified WhatsApp bot that consolidates the existing wa_pull + wa_slash_commands
components into a single project with a personal assistant in MeChat, conversation continuity
awareness, and a hot-word-driven deep-dive search system across all non-archived chats.

### Project Structure

```
master_project/
├── README.md                          # this project overview
├── scripts/
│   └── hermes_start.sh                # single start command
├── hermes_bot/                        # the unified bot (NEW — 16 files)
│   ├── main.py                        # entry point: supervisor + cron
│   ├── config.py                      # env vars & constants
│   ├── supervisor.py                  # bridge lifecycle, QR relay, crash recovery
│   ├── sender.py                      # WhatsApp message sender
│   ├── db.py                          # SQLite queries (groups, messages, contacts)
│   ├── mechat_handler.py              # CLI invoked by Go bridge on MeChat messages
│   ├── .env / .env.example            # config (GEMINI_API_KEY, OWNER_PHONE_NUMBER)
│   ├── requirements.txt               # requests, google-genai, python-dotenv
│   ├── assistant/                     # MeChat personal assistant logic
│   │   ├── session.py                 # JSON-persisted conversation sessions
│   │   ├── continuity.py              # LLM-based continuity detection
│   │   ├── handler.py                 # intent detection & routing
│   │   └── responder.py               # free-form responses, routing dispatch
│   └── cron/                          # hot-word deep dive system
│       ├── scheduler.py               # background thread, checks every 30s
│       ├── cron_store.py              # SQLite CRUD for cron jobs
│       ├── searcher.py                # 5-step deep dive pipeline
│       └── feedback.py                # feedback state machine
├── components/                        # infrastructure
│   ├── wa_bridge/                     # Go bridge (MODIFIED — MeChat trigger added)
│   ├── wa_slash_commands/             # slash command engine (used as-is)
│   └── wa_pull/                       # legacy reference (kept for compatibility)
└── docs/                              # documentation
    ├── contact_resolution.md          # JID/LID/phone → name resolution
    ├── archive_detection.md           # archived chat detection guide
    ├── whatsapp_database_master_guide.md  # DB schema reference
    └── superpowers/specs/
        └── 2026-08-07-hermes-design.md   # full design spec
```

---

## How to Start

```bash
cd /path/to/hermes
./scripts/hermes_start.sh
```

That's the only command. It:

1. Checks if the Go bridge binary (`wa-bridge`) exists; builds it if not (`GOTOOLCHAIN=go1.25.0 go build`)
2. Loads `.env` from `hermes_bot/.env` (GEMINI_API_KEY, OWNER_PHONE_NUMBER, MECHAT_JID, BRIDGE_PORT)
3. Starts the Go bridge as a subprocess (QR code on first pairing, auto-connect if paired)
4. Waits for pairing / connection
5. Once API is up on `:$BRIDGE_PORT` (8877 here), launches the cron scheduler thread
6. Sends a welcome message to MeChat
7. Blocks, monitoring bridge health (auto-restart on crash)

---

## Build Log — 2026-08-08: MeChat Not Responding (Diagnosed & Fixed)

**Symptom:** Slash commands worked, but messages sent to MeChat ("Hello there",
"Hello hello") got no response. MeChat messages never even landed in `messages.db`.

### Root causes found (3 stacked bugs)

**1. Placeholder JID in the bridge trigger (`main.go`).**
The MeChat group check compared against a literal placeholder:

```go
if !isMeChat && chatJID == "91XXXXXXXXXX-1633800754@g.us" {   // never matches!
```

The real "My notes" group JID is `91XXXXXXXXXX-1633800754@g.us`, so `mechat_handler.py`
was never spawned. (The true self-chat `@s.whatsapp.net`/`@lid` gets no usable message
events on linked devices — zero self-chat messages were ever stored.)

**2. Replies addressed to an undeliverable LID JID (Python).**
`get_mechat_chat_jid()` resolved MeChat to `219541632213229@lid` (LID-based self-chat).
Sends to it failed silently; the startup welcome message sat stuck in
`hermes_bot/store/pending_messages.json` for 10+ minutes.

**3. Port 8080 hijacked by another dev server (the real killer).**
A local Vite dev server (unrelated project) was listening on port 8080. The bridge
logged `REST API server error: listen tcp :8080: bind: address already in use` and ran
**without an API**. Every `/api/send` call (flush queue, slash replies, health checks)
hit the Vite server and got a 404. The supervisor health check counted *any* HTTP
response as "up", so startup reported success anyway. Slash commands had only worked
earlier because they were tested before the Vite server started.

### Fixes applied

| File | Change |
|------|--------|
| `components/wa_bridge/main.go` | Added `mechatJID` global; read from `MECHAT_JID` env, defaults to `<OWNER_PHONE_NUMBER>-1633800754@g.us`. Replaced the placeholder group check with `chatJID == mechatJID`. REST port now from `BRIDGE_PORT`/`PORT` env (default 8080). Added `strconv` import. |
| `hermes_bot/config.py` | Added `MECHAT_JID` env var. |
| `hermes_bot/db.py` | `get_mechat_chat_jid()` returns `config.MECHAT_JID` when set — replies go to the "My notes" group instead of the broken LID self-chat. |
| `hermes_bot/supervisor.py` | Passes `MECHAT_JID` + `BRIDGE_PORT` to the bridge env. `_bridge_api_up()` now treats HTTP 404 as *down* (a foreign server on the port no longer fools the health check). |
| `hermes_bot/main.py` | Added `_drain_bridge_output()` — a daemon thread drains bridge stdout after pairing (prevents pipe-buffer deadlock and keeps `[mechat-detect]` / handler logs visible). Re-attached on bridge restarts too. |
| `components/wa_slash_commands/wacmd.py` | Normalizes `WA_API_URL` — appends `/api/send` if the env var only has the base URL (Hermes `.env` style) so both formats work. |
| `hermes_bot/.env` / `.env.example` | Added `MECHAT_JID`, `BRIDGE_PORT="8877"`, `WA_API_URL="http://localhost:8877"`. |
| `scripts/hermes_start.sh` | Bridge build uses `GOTOOLCHAIN=go1.25.0` (whatsmeow dep requires go >= 1.25). |
| `components/wa_bridge/go.mod` | `go 1.25.0` → `go 1.24.0` directive (toolchain auto-resolves 1.25 for deps). |

### Environment changes required on this machine

```env
MECHAT_JID="91XXXXXXXXXX-1633800754@g.us"
BRIDGE_PORT="8877"
WA_API_URL="http://localhost:8877"
```

> Ports 8080 and 8081 are occupied by local node/Vite dev servers. If you add more
> dev servers, keep 8877 free — or pick another port and update all three vars.

### Verification (all passed)

- Bridge logs on startup: `Owner phone set: 91XXXXXXXXXX`, `MeChat JID: 91XXXXXXXXXX-1633800754@g.us`, `Starting REST API server on :8877...`
- Trigger fires: `[mechat-detect] ... isMeChat=true` → `>>> SPAWNING handler` for both the real self-chat and the "My notes" group.
- Send path: `curl POST :8877/api/send` → `{"success":true}`; message visible in "My notes".
- Full loop: manual `mechat_handler.py` run → reply enqueued → `[sender] Flushed 1 pending message(s)` → queue empty → reply delivered.

---

## Build Log — 2026-08-08 (evening): MeChat Silent Again — Duplicate Hermes Instance

**Symptom:** Same as the morning — MeChat messages detected (`isMeChat=true`, handler
spawned, reply generated and enqueued at 14:37:54) but nothing delivered. No
`[sender] Flushed` line ever appeared.

### Root cause

A **second, orphaned Hermes instance** was running. An earlier
`python3 -m hermes_bot.main` (started 14:31, parent PID 1 — terminal closed/backgrounded)
kept running with its own bridge, which held port 8877. When the visible instance
started (14:33):

1. Its bridge failed to bind 8877 (`listen tcp :8877: bind: address already in use`) but
   **kept running** — receives worked, but it had no REST API.
2. The stale bridge still answered `/api/send` with non-404, so `_bridge_api_up()` passed
   and startup reported "Bridge already paired and connected."
3. The stale bridge's WhatsApp connection had been replaced by the new bridge (WhatsApp
   allows only one active web session), so every real send failed silently. Replies
   (including the startup "Ready" message) accumulated in `pending_messages.json`.

Killing bridges alone did NOT help — both supervisors' auto-restart respawned bridges
and re-created the port race. The orphaned supervisor also survived SIGTERM for a while
(stuck in `_shutdown` → `scheduler.stop()`, joining a cron thread mid-LLM-job).

### Fix applied

| File | Change |
|------|--------|
| `hermes_bot/supervisor.py` | `start_bridge()` now calls `_free_bridge_port()` before spawning: finds whatever listens on the bridge port, SIGTERMs it if it's a stale `wa-bridge`, exits loudly with instructions if it's a foreign process. Port comes from `BRIDGE_PORT`, else parsed from `WA_API_URL`, default 8080. |

### Recovery performed (live)

- Killed the orphaned supervisor + stale bridges; the foreground `main.py` auto-restarted
  a healthy bridge which bound 8877 and flushed the queue (welcome + win-page reply
  delivered to "My notes").
- Verified: exactly one `main.py` and one `wa-bridge`; `/api/send` → `{"success":true}`;
  `pending_messages.json` empty.

**Lesson:** before debugging MeChat silence, first check for duplicate instances —
`pgrep -fl hermes_bot.main` and `pgrep -fl wa-bridge` must each show exactly ONE process.

---

## Architecture

### How Messages Flow

```
WhatsApp message arrives
         │
         ▼
Go Bridge (whatsmeow) writes to messages.db + whatsapp.db
         │
         ├── Message starts with "/"
         │   └── spawns wacmd.py <chat_jid> <sender_jid> <command>
         │       (slash commands: /sotu, /pending, /stats, /recap, /eli5, /help)
         │
         └── Message is from MeChat + owner + NOT a "/" command
             └── spawns mechat_handler.py <chat_jid> <sender_jid> <text>
                 │
                 ├── Loads session from hermes_bot/store/session.json
                 ├── Checks conversation continuity (LLM)
                 ├── Routes intent (LLM)
                 │   ├── /ask query → deep dive across groups
                 │   ├── /cron add/list/pause/feedback → cron management
                 │   ├── question → free-form LLM response
                 │   ├── statement → acknowledge + offer options
                 │   └── greeting/help → static responses
                 ├── Sends reply via POST :$BRIDGE_PORT/api/send (8877 here)
                 └── Saves session to session.json, exits
```

### Key Design Decisions

**Bridge-triggered, not polling.**
The Go bridge's `handleMessage()` function directly spawns `mechat_handler.py` when a
MeChat message arrives from the owner. This is the same subprocess-spawning pattern the
bridge already uses for slash commands (`wacmd.py`). Zero latency, no polling loop.

**Fresh process per message.**
`mechat_handler.py` is a CLI script that runs, processes one message, and exits. Session
state (conversation topic, recent messages, timeout) is persisted to
`hermes_bot/store/session.json` — read on startup, written on completion.

**Only the cron scheduler is long-running.**
The `hermes_bot/main.py` process only runs the bridge supervisor and the cron scheduler
thread. Everything else is stateless or file-persisted.

**MeChat = the owner's personal "notes" group.**
The WhatsApp self-chat (`@s.whatsapp.net` / `@lid`) does not deliver usable message
events to linked devices, and sends to LID JIDs fail. So MeChat is the owner's
single-member "My notes" group, identified by `MECHAT_JID` (default:
`<OWNER_PHONE_NUMBER>-1633800754@g.us`). The bridge matches incoming messages against
this JID; replies are sent to the same JID. Both directions verified working.

---

## What Was Changed in the Go Bridge

**File:** `components/wa_bridge/main.go` — changes:

1. Added `var ownerPhone string` and `var mechatJID string` globals.
2. In `main()`: reads `OWNER_PHONE_NUMBER` and `MECHAT_JID` env vars on startup.
   `MECHAT_JID` defaults to `<OWNER_PHONE_NUMBER>-1633800754@g.us` (owner's notes group).
3. In `handleMessage()`, after the existing slash-command handler block, a MeChat trigger:
   - `isMeChat` if `chatUser == senderUser` (true self-chat), or `chatUser == ownerPhone`,
     or `chatJID == mechatJID` (owner's notes group)
   - Only when the message doesn't start with `/`
   - Spawns `../../hermes_bot/mechat_handler.py <chat_jid> <sender_jid> <text>`
   - Runs in a goroutine (non-blocking)
   - Emits `[mechat-detect]` debug lines for every non-slash message
4. REST API port is configurable via `BRIDGE_PORT` (or `PORT`) env — default 8080.
   This machine runs it on **8877** because local dev servers occupy 8080/8081.

---

## What Was Removed

These components were deleted because they're not part of the Hermes project:

- `components/channel_manager/` — multi-channel routing manager
- `components/master_tracker/` — initiative/OKR tracker
- `components/wa_brain/` — brain writer helper
- `components/wa_productivity/` — legacy analysis scripts
- `scripts/` (old) — legacy deploy/EC2/sync scripts (replaced with `scripts/hermes_start.sh`)
- 13 stale docs from `docs/` — EC2 guide, backend docs, frontend docs, troubleshooting, etc.

---

## Feature Details

### 1. Slash Commands (unchanged from existing)

Works in any chat. Owner-only (enforced by `wacmd.py:390-407`).

| Command | What it does |
|---------|-------------|
| `/sotu` | State of the Union — group summary |
| `/pending` | Pending action items |
| `/stats` | Team activity stats |
| `/recap` | Last 24 hours recap |
| `/eli5 <topic>` | Explain topic in detail |
| `/help` | Show available commands |

### 2. MeChat Personal Assistant

**Conversation continuity:**
- Each conversation is a "session" with a topic label
- On each message, an LLM call checks: is this continuing the current topic?
  - High confidence (≥0.7) yes → continue session, respond with context
  - Low confidence (0.4–0.7) → ask user "continue or start fresh?"
  - No → close old session, start new
- Sessions auto-timeout after 60 minutes of inactivity
- Session state persisted to `hermes_bot/store/session.json`

**Intent routing:**

| User sends | Hermes does |
|-----------|------------|
| `hi`, `hello`, `hey` | Greeting |
| `help`, `/help` | Help text with available commands |
| `/ask <query>` | One-shot deep dive across groups |
| `/cron add "<query>" daily 09:00` | Create recurring cron job |
| `/cron list` | List active cron jobs |
| `/cron pause <id>` | Pause a cron job |
| `/cron resume <id>` | Resume a cron job |
| `/cron delete <id>` | Delete a cron job |
| `/cron feedback <id> "feedback"` | Give feedback on last run |
| Natural language question | LLM free-form response |
| Natural language (query-like) | May trigger deep dive if LLM detects it |

### 3. Hot-Word Deep Dive (`/ask`)

5-step pipeline:

1. **List groups** — query all non-archived groups active in last 30 days
2. **Score relevance** — batch groups into 10s, LLM scores each 0-10 for relevance to query
3. **Deep fetch** — get last 200 messages from top-N relevant groups
4. **Per-group summarize** — LLM summarizes each group's relevant content in 3-5 bullets
5. **Cross-group synthesize** — LLM produces overall picture, key items, blockers, contradictions

Every response includes a methodology footer:
```
🔍 Methodology
Scanned: 47 groups · Matched: 12 relevant
Lookback: 14 days · Messages: ~200/group
Relevance filter: "UPI growth"
Deep-dived: top 8 groups
```

### 4. Cron Jobs

**State machine:**
```
active → runs → awaiting_feedback
  ├── user likes → saved (continues on schedule)
  └── user dislikes → feedback_collected → retry_pending → retries with feedback
```

**Storage:** `hermes_bot/store/hermes.db` (SQLite) — `cron_jobs` and `cron_run_log` tables

**Scheduling:** Background thread checks every 30 seconds for due jobs. Supports
`daily`, `weekdays`, `weekly:<day>`, and `oneshot` frequencies.

**Feedback loop:** When user gives feedback on a run, it's injected into the LLM
synthesis prompt on the next run. Example: "User said last summary was too broad —
focus only on specific decisions."

---

## Environment Variables

**`hermes_bot/.env`** (configured):

```env
GEMINI_API_KEY=...           # Gemini API key
OWNER_PHONE_NUMBER=91XXXXXXXXXX
MECHAT_JID=91XXXXXXXXXX-1633800754@g.us   # owner's "My notes" group = MeChat

# Bridge API (this machine: 8080/8081 are taken by local dev servers)
BRIDGE_PORT=8877
WA_API_URL=http://localhost:8877

# Defaults below work out of the box:
# MESSAGES_DB_PATH=components/wa_bridge/store/messages.db
# WHATSAPP_DB_PATH=components/wa_bridge/store/whatsapp.db
# HERMES_DB_PATH=hermes_bot/store/hermes.db
# SESSION_TIMEOUT_MINUTES=60
# MAX_GROUPS_PER_SEARCH=10
# SEARCH_LOOKBACK_DAYS=14
# CRON_ENABLED=1
```

---

## Dependencies

**Python** (`hermes_bot/requirements.txt`):
- `requests>=2.31.0` — bridge API calls
- `google-genai>=0.3.0` — Gemini LLM
- `python-dotenv>=1.0.0` — .env loading

**Go** (`components/wa_bridge/go.mod`):
- `go.mau.fi/whatsmeow` — WhatsApp Web client
- `github.com/mattn/go-sqlite3` — SQLite driver
- `github.com/mdp/qrterminal` — QR code rendering

---

## Key Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| `hermes_bot/main.py` | 65 | Entry point, bridge lifecycle, signal handling |
| `hermes_bot/supervisor.py` | 231 | Bridge subprocess, QR relay, pairing wait, API health check, stale-bridge port cleanup |
| `hermes_bot/mechat_handler.py` | 90 | CLI script: loads session → continuity → intent → reply → save |
| `hermes_bot/assistant/session.py` | 155 | SessionManager class, JSON persistence, timeout logic |
| `hermes_bot/assistant/continuity.py` | 55 | LLM call: "is this continuing the same conversation?" |
| `hermes_bot/assistant/handler.py` | 90 | LLM call: detect intent (ask/cron/question/statement/etc.) |
| `hermes_bot/assistant/responder.py` | 95 | Route intents to handlers, free-form LLM responses, help text |
| `hermes_bot/cron/searcher.py` | 180 | 5-step pipeline: groups → relevance → fetch → summarize → synthesize |
| `hermes_bot/cron/cron_store.py` | 190 | SQLite CRUD for cron_jobs + cron_run_log tables |
| `hermes_bot/cron/scheduler.py` | 95 | Background thread, 30s tick, cron execution + feedback delivery |
| `hermes_bot/cron/feedback.py` | 115 | Handle /cron add/list/pause/feedback/keep commands |
| `hermes_bot/db.py` | 180 | All SQLite queries (non-archived groups, messages, contacts, MeChat JID) |
| `hermes_bot/sender.py` | 55 | POST to bridge API, long message splitting, retry logic |
| `hermes_bot/config.py` | 46 | Env var loading, path defaults, model config, `MECHAT_JID` |
| `components/wa_bridge/main.go` | 1464 | Go bridge — MeChat trigger, `MECHAT_JID`/`BRIDGE_PORT` env support |

---

## Operations Cheatsheet

```bash
# Start (builds bridge if missing, pairs if needed)
./scripts/hermes_start.sh

# Watch live logs (when started via nohup/background)
tail -f /tmp/hermes.log

# Check the bridge API is REALLY ours (not another server on the port)
curl -s -X POST http://localhost:8877/api/send \
  -H "Content-Type: application/json" \
  -d '{"recipient":"91XXXXXXXXXX-1633800754@g.us","message":"ping"}'
# Expect: {"success":true,...}.  A 404 = wrong process owns the port.

# See who owns the bridge port
lsof -nP -iTCP:8877 -sTCP:LISTEN

# Check for stuck outbound messages (should normally be [] or absent)
cat hermes_bot/store/pending_messages.json

# Latest messages the bridge stored (verify receiving works)
sqlite3 components/wa_bridge/store/messages.db \
  "SELECT chat_jid, content, timestamp FROM messages ORDER BY timestamp DESC LIMIT 5;"

# Messages in MeChat ("My notes" group)
sqlite3 components/wa_bridge/store/messages.db \
  "SELECT sender, content, timestamp FROM messages
   WHERE chat_jid='91XXXXXXXXXX-1633800754@g.us' ORDER BY timestamp DESC LIMIT 10;"

# Manually simulate a MeChat message (tests full Python pipeline)
python3 hermes_bot/mechat_handler.py \
  "91XXXXXXXXXX-1633800754@g.us" "91XXXXXXXXXX@s.whatsapp.net" "hello"
```

**If MeChat stops responding, check in order:**
0. **Duplicate instances** — `pgrep -fl hermes_bot.main` and `pgrep -fl wa-bridge` must
   each show exactly ONE process. A second instance means its bridge stole the port;
   kill the extra supervisor *and* its bridge (killing the bridge alone doesn't work —
   its supervisor respawns it). New starts self-heal this via `_free_bridge_port()`.
1. `[mechat-detect]` lines in the bridge log — if absent, the message never reached
   the bridge (connection) or the JID didn't match `MECHAT_JID`.
2. `pending_messages.json` growing — send path broken; test `/api/send` with curl
   and check the port isn't hijacked (`lsof`).
3. `[hermes-handler] ERROR` in logs — the Python handler crashed; run it manually
   (see above) to see the traceback.