# context_doc.md — wa-slash-commands Project Context

> **Written for handoff.** This document contains full context for anyone picking up this project — the goal, what was built, the current state, and the unresolved issue with QR code authentication.

---

## 1. Project Goal

**wa-slash-commands** is a standalone, open-source-ready Python project that adds AI-powered slash commands to any WhatsApp chat. It is extracted from a private EC2-hosted setup and designed to be:

- Cloned from GitHub by anyone
- Set up in under 5 minutes via `python3 setup.py`
- Activated only for the **owner's phone number** — commands from any other number in a group are silently ignored
- Powered exclusively by Google's Gemini API (free tier supported)

The project is located at:
```
/path/to/your/project/attempt_v5/sandbox/wa-slash-commands/
```

---

## 2. How It Works (Architecture)

The project hooks into a **whatsmeow Go bridge** that runs as a local HTTP server and maintains the WhatsApp Web connection. When a message starting with `/` is received, the bridge calls `wacmd.py` as a subprocess.

```
WhatsApp App (phone)
       ↕ (WhatsApp Web protocol)
whatsmeow bridge (Go binary)               ← at ../whatsapp-bridge/wa-bridge-arm64
       │ writes to SQLite
       ↓
store/messages.db + store/whatsapp.db      ← local to this project
       │ read by
       ↓
wacmd.py <chat_jid> <sender_jid> <cmd>    ← called by bridge on /command
       │
       ├── engine.py                        ← reads DB, calls Gemini API
       ├── formatter.py                     ← formats WhatsApp-ready output
       └── POST localhost:8080/api/send     ← bridge sends reply back to chat
```

**Owner lock**: `wacmd.py` reads `OWNER_PHONE_NUMBER` from `.env`. If `sender_jid` does not match the owner's number, the script silently exits — no AI is called, no reply is sent.

---

## 3. Files in the Project

| File | Purpose |
|---|---|
| `setup.py` | Interactive CLI wizard: collects Gemini key, phone #, launches bridge for QR scan, writes `.env` |
| `wacmd.py` | Main entry point — owner check, command dispatch |
| `engine.py` | Gemini API integration + SQLite message fetching |
| `formatter.py` | WhatsApp message formatting templates |
| `test_local.py` | Standalone test harness — seeds a mock DB, monkeypatches send_message, runs all commands without a live bridge |
| `.env.example` | Template for user configuration |
| `requirements.txt` | `google-generativeai`, `python-dotenv`, `requests` |
| `README.md` | GitHub showcase README |
| `SETUP.md` | Two-step quick start |
| `project_manual.md` | Extraction context (from EC2 to standalone) |

**Runtime-generated (not in git):**
- `.env` — created by `setup.py`
- `store/` — created by the bridge on first auth

---

## 4. Commands Supported

| Command | What it does | Time window |
|---|---|---|
| `/help` | Lists all commands | — |
| `/sotu` | State of the Union — purpose, themes, momentum | 30 days |
| `/pending` | Open loops, next steps with owners | 30 days |
| `/stats` | Team personas, activity shares, bottlenecks | 14 days |
| `/recap` | 24h signal-only timeline (noise filtered) | 24 hours |
| `/eli5 <topic>` | Explains any topic using chat as context | 30 days |

All commands use **Gemini strict JSON schema enforcement** (`response_schema`) to prevent hallucination and ensure parseable output.

---

## 5. The Unresolved Issue: QR Code Not Appearing

### What the user sees

```
── Step 3: WhatsApp Authentication ─────────────────────────
  Starting WhatsApp bridge…
  ────────────────────────────────────────────────────────
11:24:18.138 [Client INFO] Starting WhatsApp client...
11:24:19.662 [Client/Socket ERROR] Error reading from websocket: failed to get reader: failed to read frame header: EOF
  ────────────────────────────────────────────────────────
  ✓ WhatsApp connected. Databases synced ✨
```

No QR code ever appears. The bridge immediately errors, exits, but setup reports success.

### Root Cause (Confirmed)

The `whatsmeow` bridge binary (`../whatsapp-bridge/wa-bridge-arm64`) creates the SQLite database **schema files** (`store/whatsapp.db`, `store/messages.db`) **immediately on startup**, before any authentication happens. Specifically:

- `store/whatsapp.db` is created with 15 tables (all empty) right away
- `whatsmeow_device` table has **0 rows** — meaning no WhatsApp session is stored
- The bridge then tries to connect to WhatsApp's websocket
- It gets an EOF / connection error (likely the session is invalid or already in use)
- It quits immediately

Our `_dbs_exist()` check in `setup.py` only checks if the **files exist** — not whether there's a valid session inside. So it sees the two files exist and incorrectly reports "connected."

**Verified with:**
```python
# whatsmeow_device: 0 rows  ← no actual WhatsApp session
# whatsmeow_contacts: 3 rows ← populated by test_local.py seeding earlier
```

### Why no QR appears

The bridge never reaches the QR display code path because:
1. It finds `store/whatsapp.db` already exists (we create it before launching)
2. It tries to load a session from `whatsmeow_device` — finds 0 rows
3. It should then generate a QR code, but instead gets a websocket EOF immediately

The websocket EOF suggests either:
- A conflicting bridge process is already running on port 8080 (the production bridge from the EC2 setup or `whatsapp-mcp`)
- The network/firewall is blocking the WhatsApp Web websocket

### The Correct Fix (not yet implemented)

**Option A — Check for valid session, not just file existence:**
```python
def _has_valid_session():
    db = os.path.join(_db_store_dir(), "whatsapp.db")
    if not os.path.exists(db):
        return False
    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM whatsmeow_device").fetchone()[0]
    conn.close()
    return count > 0
```
Replace all `_dbs_exist()` calls with `_has_valid_session()`.

**Option B — Kill any conflicting bridge before launching:**
```python
import subprocess
subprocess.run(["pkill", "-f", "wa-bridge"], capture_output=True)
time.sleep(1)
# then launch fresh
```

**Option C — Pass a `-port` flag to run bridge on a different port** (avoids conflict with existing bridge on 8080).

### Suspected Primary Cause

The existing production `whatsmeow` bridge (running for the EC2/main project on port 8080) is likely already holding the same WhatsApp session, causing the new bridge instance to fail with EOF when it tries to re-establish the same session from a different process.

The correct onboarding flow for a **truly fresh user** should work fine — they won't have another bridge running. The issue is specific to this machine because there's already an authenticated bridge running.

---

## 6. The Working Test Flow

Even though the QR flow has issues in this environment, the core slash commands work correctly. Verified via `test_local.py`:

```bash
python3 test_local.py --cmd /help
# Owner block fires correctly for wrong number
# /help response renders correctly
# Gemini API key required for AI commands (/sotu, /pending, /recap, /stats, /eli5)
```

---

## 7. Next Steps for Whoever Picks This Up

1. **Fix `_dbs_exist()` → `_has_valid_session()`** (check `whatsmeow_device` row count, not just file existence)
2. **Kill conflicting bridge processes** before launching the new one in `step_whatsapp_auth()`
3. **Test on a clean machine** (no pre-existing whatsmeow bridges running) to confirm QR renders correctly for a new user
4. Once QR flow works end-to-end, push `sandbox/wa-slash-commands/` as a public GitHub repo
