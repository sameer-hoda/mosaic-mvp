# Hermes — WhatsApp Personal Assistant System Design

> **Status:** Draft · First working prototype
> **Date:** 2026-08-07
> **Context:** Consolidates and extends wa_pull + wa_slash_commands + ops into a single
> unified WhatsApp bot with personal assistant capabilities in MeChat, conversation
> continuity awareness, and a hot-word-driven deep-dive search system across all
> non-archived chats.

---

## 1. Product Summary

**What it does:** A single `hermes start` command launches the entire system. If the
WhatsApp bridge is not paired, a QR code appears in the terminal. Once scanned, all
interaction moves to WhatsApp — the terminal is no longer needed.

**The user experiences two surfaces:**

| Surface | Trigger | What happens |
|---------|---------|--------------|
| **Slash commands** (any chat) | User types `/sotu`, `/pending`, `/stats`, `/recap`, `/eli5`, `/help` | Immediate formatted reply in the same chat. Existing behavior, preserved exactly. |
| **MeChat assistant** (the chat with yourself) | User sends any message to MeChat | Hermes responds as a personal assistant. Understands conversation continuity. Accepts natural-language requests, `/ask` hot-word deep dives, and cron scheduling commands. |

**Key principles:**
- Brevity + formatting: uses WhatsApp markdown (`*bold*`, `_italic_`, `~strikethrough~`,
  emoji, clean section breaks). Never verbosity for its own sake.
- Only the user/owner can trigger any behavior. No one else's messages produce bot output.
- All group chat context is read-only — Hermes reads messages to summarize but does not
  reply in groups unless explicitly told to (future).

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     hermes start                             │
│                         │                                    │
│         ┌───────────────┴───────────────┐                    │
│         ▼                               ▼                    │
│  ┌─────────────────┐           ┌──────────────────┐         │
│  │   Go Bridge      │           │  Hermes Python   │         │
│  │   (whatsmeow)    │           │  Supervisor      │         │
│  │                  │           │                  │         │
│  │ • QR display     │◄─health──│ • waits for bridge│         │
│  │ • :8080 API      │  check   │ • once ready:     │         │
│  │ • writes DBs     │           │   runs cron thread│         │
│  │                  │           │   blocks on signal│         │
│  │ • on / → spawns  │           └──────────────────┘         │
│  │   wacmd.py       │                                        │
│  │ • on MeChat msg  │                                        │
│  │   from owner →   │                                        │
│  │   spawns hermes  │                                        │
│  │   handler.py     │                                        │
│  └──────┬───────────┘                                        │
│         │                                                    │
│         │  store/messages.db                                 │
│         │  store/whatsapp.db                                 │
│         │                                                    │
│         │     ┌──────────────────────────┐                   │
│         │     │ Hermes handler.py (CLI)   │                   │
│         │     │                           │                   │
│         │     │ • reads DBs (read-only)   │                   │
│         │     │ • continuity check (LLM)  │                   │
│         │     │ • intent routing (LLM)    │                   │
│         │     │ • hot-word deep dive      │                   │
│         │     │ • sends reply via         │                   │
│         │     │   POST :8080/api/send     │                   │
│         │     └──────────────────────────┘                   │
│         │                                                    │
│         │     ┌──────────────────────────┐                   │
│         │     │  Cron Engine (daemon)     │                   │
│         │     │                           │                   │
│         │     │ • APScheduler thread      │                   │
│         │     │ • triggers deep dive      │                   │
│         │     │ • feedback state machine  │                   │
│         │     │ • sends via :8080/api/send│                   │
│         │     │ • writes hermes.db        │                   │
│         │     └──────────────────────────┘                   │
└──────────────────────────────────────────────────────────────┘
```

### How it works (two trigger paths, both from the bridge)

| Trigger | Detected by | Action |
|---------|------------|--------|
| Message starts with `/` (any chat, owner-only) | Go bridge `handleMessage()` | Spawns `wacmd.py <chat_jid> <sender_jid> <command>` as subprocess. Existing behavior, unchanged. |
| Message in MeChat from owner (non-`/` message) | Go bridge `handleMessage()` | Spawns `hermes_handler.py <chat_jid> <sender_jid> <message_text>` as subprocess. **New.** |

### Sub-services (run from the Hermes Python process)

| Service | Role | Trigger | DB writes? |
|---------|------|---------|------------|
| **Bridge Supervisor** | Manages bridge lifecycle, QR relay, health check, crash recovery | Runs on startup, monitors bridge process | No |
| **Cron Engine** | Schedules and executes hot-word deep dives, manages feedback loops | APScheduler daemon thread, checks every 30s | Yes (hermes.db) |
| **Hermes Handler** | Responds to MeChat messages with continuity, intents, deep dives | Invoked by bridge as subprocess per message | No (read-only) |
| **Slash Commands** | Handles `/sotu`, `/pending`, `/stats`, `/recap`, `/eli5`, `/help` | Invoked by bridge as subprocess per `/` message | No (existing, unchanged) |

### Why bridge-triggered instead of polling

1. **Zero latency.** The bridge fires the handler the instant a MeChat message arrives — no 3-second polling gap.
2. **Consistent with slash commands.** Same subprocess-spawning pattern the bridge already uses for `/` messages. Just a different script for a different chat.
3. **No polling code.** The Hermes Python process doesn't need a MeChat polling loop, timestamp tracking, or bot-message filtering. Those concerns move to the bridge.
4. **Stateless handler.** Each MeChat message gets a fresh Python invocation. Session state (conversation continuity) is persisted to a small JSON file, read on each invocation. No long-lived in-memory state to lose on crash.
5. **Cheaper when idle.** When no one is messaging MeChat, the only thing running is the cron scheduler thread (lightweight clock check). No constant DB queries.

---

## 3. Component Designs

### 3.1 Startup & Bridge Lifecycle (`hermes_bot/supervisor.py`)

**On `hermes start`:**

1. Check for `store/whatsapp.db`. If absent, bridge is not paired.
2. Launch Go bridge as subprocess, capture stdout.
3. If bridge outputs QR code text (contains `▀` or `qrterminal` output), relay to terminal clearly: `📱 Scan this QR code with WhatsApp (Settings → Linked Devices):`
4. Poll `store/whatsapp.db` for `SELECT COUNT(*) FROM whatsmeow_device` every 2 seconds until a row appears (up to 3-minute timeout — matches bridge's QR timeout).
5. Once paired, bridge prints "Connected" — Hermes prints `✅ Paired. Hermes is live. All interaction now on WhatsApp.`
6. Bridge's REST API is ready on `:8080`.
7. Hermes launches all sub-services as daemon threads.
8. Sends a welcome message to MeChat: `🤖 *Hermes* · Ready · /help for commands`
9. Blocks on main thread, handles SIGINT for graceful shutdown.

**On subsequent starts (already paired):**
1. `store/whatsapp.db` exists → skip QR.
2. Launch bridge as subprocess, wait for `:8080` to accept connections.
3. Launch sub-services.
4. Welcome message: `🤖 *Hermes* · Back online`

**Bridge crash recovery:**
- Supervisor monitors bridge process. If it exits unexpectedly:
  - Restart with exponential backoff (1s, 2s, 4s, max 30s).
  - If bridge was logged out (`whatsmeow_device` rows deleted), show QR again.
  - Send status message to MeChat: `⚠️ *Bridge restarted* · Brief interruption`

### 3.2 MeChat Assistant (`hermes_bot/mechat_handler.py` + `hermes_bot/assistant/`)

#### 3.2.1 Bridge Integration — Triggering the Handler

The Go bridge's existing `handleMessage()` function (`main.go:414`) already spawns a
Python subprocess when it sees a `/` message. We extend this with a second trigger:

```go
// Pseudocode for the new logic in main.go handleMessage():
if strings.HasPrefix(messageText, "/") {
    // Existing: spawn wacmd.py for slash commands
    exec.Command("python3", "wacmd.py", chatJID, senderJID, messageText).Run()
} else if isMeChat(chatJID) && isOwner(senderJID) {
    // NEW: spawn hermes_handler.py for MeChat messages from owner
    exec.Command("python3", "hermes_bot/mechat_handler.py", chatJID, senderJID, messageText).Run()
}
// Else: regular message, no action needed
```

**Owner detection in the bridge:** The bridge reads `OWNER_PHONE_NUMBER` from the same
`.env` file. It constructs the owner JID as `{phone}@s.whatsapp.net` and compares it to
`senderJID` (with device-suffix stripped). This matches the existing owner check in
`wacmd.py:390-407`.

**MeChat detection in the bridge:** The bridge can identify MeChat by:
1. Querying `whatsmeow_lid_map` where `pn = OWNER_PHONE_NUMBER` to get the owner's LID.
2. Comparing `chatJID` to `{lid}@lid` (the MeChat JID format).
3. Fallback: comparing `chatJID.User == OWNER_PHONE_NUMBER`.

**Handler invocation:** `hermes_handler.py` is a CLI script with the same contract as
`wacmd.py`:
```bash
python3 hermes_bot/mechat_handler.py <chat_jid> <sender_jid> <message_text>
```

It runs synchronously — the bridge waits for it to complete before processing the next
message. This is the same blocking behavior as slash commands and is acceptable because:
- WhatsApp message delivery is sequential anyway.
- The handler sends its reply via `POST :8080/api/send` (not stdout), so the bridge
  doesn't need to capture output.
- If the handler takes 30+ seconds (deep dive), the bridge blocks but no messages are
  lost — WhatsApp queues them.

#### 3.2.2 Handler Entry Point (`mechat_handler.py`)

```python
#!/usr/bin/env python3
"""
Invoked by the Go bridge when a MeChat message arrives from the owner.
Usage: python3 mechat_handler.py <chat_jid> <sender_jid> <message_text>
"""
import sys
from hermes_bot.assistant.session import SessionManager
from hermes_bot.assistant.continuity import check_continuity
from hermes_bot.assistant.handler import route_intent
from hermes_bot.sender import send_to_mechat

def main():
    chat_jid = sys.argv[1]
    sender_jid = sys.argv[2]
    message_text = sys.argv[3]

    # 1. Filter: skip bot's own messages (is_from_me check via DB)
    if is_own_message(chat_jid, sender_jid):
        return

    # 2. Load or create session from persistent JSON
    session_mgr = SessionManager()
    session = session_mgr.get_active_session()

    # 3. Continuity check (if session exists)
    if session:
        result = check_continuity(session, message_text)
        if not result.continues:
            session_mgr.close_session(session)
            session = session_mgr.create_session(message_text)
        elif result.confidence < 0.7:
            send_to_mechat(
                f"We were chatting about *{session.topic}*.\n"
                f"Want to continue that or start fresh?"
            )
            session_mgr.set_awaiting_confirmation(session, result)
            return
    else:
        session = session_mgr.create_session(message_text)

    # 4. Detect intent and route
    reply = route_intent(session, message_text)

    # 5. Send reply
    send_to_mechat(reply)

    # 6. Persist session
    session_mgr.save()
```

#### 3.2.3 Session & Continuity (`assistant/session.py`, `assistant/continuity.py`)

**What a session is:** A short-lived conversation thread with a topic label.
Example: `"discussing quarterly OKRs"`, `"planning product launch"`, `"hotword: UPI growth"`.

**Persistence model:** Since `mechat_handler.py` is invoked as a fresh process per
message, session state is persisted to a JSON file on disk (`store/session.json`).
Read on startup of each handler invocation, written on completion.

**Data structure:**
```python
{
    "active_session": {
        "session_id": "abc123",
        "topic": "discussing Q3 product priorities",
        "started_at": "2026-08-07T14:30:00+05:30",
        "last_message_at": "2026-08-07T14:35:00+05:30",
        "message_count": 12,
        "recent_messages": ["...", "..."],  # last 10 user messages (text only)
        "timeout_at": "2026-08-07T15:35:00+05:30",
        "state": "active" | "awaiting_continuity_confirm"
    },
    "archived_sessions": [...]  # last 5 closed sessions for context
}
```

**Continuity detection (`continuity.py`):**

On each new user message in MeChat:

1. If no active session → start new session, respond directly.
2. If active session exists → use lightweight LLM call to judge:
   ```
   System: You are a conversation continuity detector.
   
   Current conversation topic: {topic}
   Last 5 messages: {recent_messages}
   
   New message: {new_message}
   
   Is the new message continuing the same conversation?
   Respond with JSON: {"continues": true/false, "confidence": 0.0-1.0, "new_topic": "..."}
   ```

3. Decision matrix:
   | Continues | Confidence | Action |
   |-----------|-----------|--------|
   | true | ≥0.7 | Continue session, respond with context |
   | true | 0.4–0.7 | Ask: `We were chatting about _{topic}_. Want to continue that or start fresh?` |
   | false | any | Close old session, start new, respond as new chat |
   | true/false | <0.4 | Treat as low-confidence case (ask user) |

4. Session timeout: 60 minutes of inactivity → auto-close. Next message starts fresh.

**Why LLM and not embeddings:** For a first prototype, the LLM approach is simpler,
requires no vector store, and handles semantic understanding (e.g., "what about the
other thing" still relates to the current topic). Embeddings can be added later for
optimization.

#### 3.2.4 Intent Routing (`assistant/handler.py`)

After continuity check, route the message:

```
User message
  ├── Starts with / → treat as slash command (handled by bridge)
  ├── "/ask <query>" or "/search <query>" → hot-word deep dive (one-shot)
  ├── "/cron ..." → cron management (see §3.3)
  ├── "/help" → help text
  ├── Natural language that looks like a question/request → free-form assistant
  └── Natural language that looks like a statement/note → acknowledge, offer options
```

**Intent detection:** Use a fast Gemini call with a strict JSON schema:

```json
{
  "intent": "ask" | "cron_setup" | "cron_feedback" | "question" | "statement" | "command",
  "query": "the extracted query or topic",
  "needs_context": true/false,
  "context_scope": "mechat_only" | "all_groups" | "specific_group",
  "group_hint": "partial group name if detected"
}
```

If `intent == "command"` and the text starts with `/`, let the bridge handle it (it's
already being processed by wacmd.py). Hermes should NOT double-respond.

#### 3.2.5 Free-Form Assistant Responses (`assistant/responder.py`)

For `intent == "question"` or `intent == "statement"`:

1. If `needs_context: false` → respond directly with LLM.
2. If `needs_context: true` and `context_scope: "all_groups"` → this becomes a hot-word
   search (delegate to cron engine's one-shot path, §3.4).
3. If `context_scope: "specific_group"` → fetch recent messages from that group, include
   as context, respond.

**Response format conventions:**
- Use WhatsApp markdown: `*bold*` for key terms, `_italic_` for emphasis.
- Use emoji sparingly as section markers (📍 🎯 ⚠️ ✅).
- Limit to 3-4 bullet groups max. Brevity is a feature.
- For multi-part responses, number them: `1/3 · ...`

### 3.3 Cron Engine (`hermes_bot/cron/`)

#### 3.3.1 What a Cron Job Is

A recurring hot-word deep dive. User defines:
- **Query / hot word:** e.g., "UPI growth", "Q3 hiring", "product launch"
- **Frequency:** daily, weekdays, weekly (specific day), or one-shot
- **Time slot:** e.g., "09:00 IST", "18:30 IST"
- **Scope:** all non-archived groups, or specific group(s)

**State machine:**
```
                    ┌──────────┐
            ┌──────►│ active   │◄─────────┐
            │       └────┬─────┘          │
            │            │                │
     "like" │     ┌──────▼──────┐  "don't │
            │     │ awaiting    │  like"  │
            │     │ feedback    │─────────┤
            │     └─────────────┘         │
            │                            │
     ┌──────┴──────┐            ┌────────┴────────┐
     │ recurring   │            │ feedback_        │
     │ (saved)     │            │ collected        │
     └─────────────┘            └────────┬────────┘
                                         │
                                  ┌──────▼──────┐
                                  │ retry_      │
                                  │ pending     │────► next scheduled run
                                  └─────────────┘
```

#### 3.3.2 Cron Job Storage (`cron_store.py`)

SQLite table (hermes's own `store/hermes.db`):

```sql
CREATE TABLE cron_jobs (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    frequency TEXT NOT NULL,       -- "daily", "weekdays", "weekly:monday", "oneshot"
    time_slot TEXT NOT NULL,       -- "HH:MM" in IST
    scope TEXT DEFAULT 'all',      -- "all" or comma-separated group JIDs
    status TEXT DEFAULT 'active',  -- "active", "awaiting_feedback", "feedback_collected",
                                   -- "retry_pending", "paused", "archived"
    feedback TEXT,                 -- user's feedback text from "don't like"
    feedback_iteration INTEGER DEFAULT 0,
    last_run_at TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE cron_run_log (
    id TEXT PRIMARY KEY,
    cron_job_id TEXT NOT NULL,
    run_at TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    methodology TEXT,
    groups_scanned INTEGER,
    groups_matched INTEGER,
    user_rating TEXT,              -- "like", "dislike", null (no feedback yet)
    user_feedback TEXT,
    FOREIGN KEY (cron_job_id) REFERENCES cron_jobs(id)
);
```

#### 3.3.3 Scheduler (`scheduler.py`)

Uses APScheduler (same as ops backend). Runs as daemon thread.

**Every 30 seconds:**
1. Query `cron_jobs WHERE status IN ('active', 'retry_pending') AND next_run_at <= now()`.
2. For each due job, launch the deep-dive pipeline (§3.4).
3. If job is `retry_pending` and has `feedback`, incorporate feedback into the LLM prompt
   (e.g., "User said last summary was too broad — focus only on specific decisions").

#### 3.3.4 Cron Job Management via WhatsApp

Users manage cron jobs through MeChat:

| User sends | Hermes does |
|-----------|------------|
| `/cron list` | Lists all active cron jobs with IDs, queries, and schedules |
| `/cron add "UPI growth" daily 09:00` | Creates a new daily cron |
| `/cron add "hiring" weekdays 18:00` | Creates a weekday cron |
| `/cron pause <id>` | Pauses the cron job |
| `/cron resume <id>` | Resumes a paused cron |
| `/cron delete <id>` | Deletes (archives) a cron job |
| `/cron feedback <id> "too broad, focus on decisions"` | Adds feedback to retry_pending job |

Or through natural language:

| User sends | Hermes does |
|-----------|------------|
| "send me a summary of UPI growth every morning at 9" | Creates cron, confirms |
| "stop the hiring summary" | Pauses/deletes matching cron, confirms |
| "that last UPI summary was too long" | Tags last run with feedback, sets retry_pending |

Hermes confirms every cron mutation with a short message: `✅ *Cron set* · "UPI growth" · Daily 09:00 IST`

### 3.4 Hot-Word Deep Dive Pipeline (`hermes_bot/cron/searcher.py`)

This is the core intelligence. Called both by cron jobs and by one-shot `/ask` commands.

#### Pipeline:

```
Step 1: Identify candidate groups
  └── Query all non-archived groups (scanner.list_non_archived_groups)
      Filter: has messages in last 30 days
      Result: list of (group_jid, group_name, last_message_time)

Step 2: Relevance scoring (LLM)
  └── Batch groups into sets of 10
      For each batch, ask Gemini:
        "Given the query '<query>', which of these WhatsApp groups are relevant?
         Group names: [list]
         Respond with JSON: [{"group_jid": "...", "relevance": 0-10, "why": "..."}]
         Only include groups with relevance >= 5."
      Merge results → ranked list of relevant groups

Step 3: Deep fetch from relevant groups
  └── For each relevant group (top N, max 10):
      Fetch last 200 messages (or 14-day window, whichever is smaller)
      Resolve sender names via contacts
      Format: "[HH:MM] Sender: content"

Step 4: Per-group summarization (LLM)
  └── For each group:
      "Here are recent messages from the WhatsApp group '<group_name>'.
       Filter for information relevant to: '<query>'.
       Ignore unrelated discussions.
       Summarize in 3-5 bullet points. Focus on decisions, action items,
       status updates, and blockers.
       
       Messages:
       {formatted_messages}"
      
      Result: per-group summary (3-5 bullets)

Step 5: Cross-group synthesis (LLM)
  └── Combine all per-group summaries:
      "You analyzed {N} WhatsApp groups for relevance to '<query>'.
       Per-group summaries below. Produce a cross-group synthesis:
       
       1. 🎯 Overall picture (2-3 sentences)
       2. 📋 Key items across groups (top 5-7 bullets)
       3. ⚠️ Blockers / risks (if any)
       4. 🔄 Contradictions (if groups disagree)
       
       Per-group summaries:
       {all_summaries}"
```

#### Methodology Reporting

Every deep dive response includes a methodology footer:

```
━━━━━━━━━━━━━━━━━━
🔍 *Methodology*
Scanned: 47 groups · Matched: 12 relevant
Lookback: 14 days · Messages: ~2,400
Relevance filter: "UPI growth"
Deep-dived: top 8 groups by relevance
```

#### One-Shot vs Cron Output Format

**One-shot (`/ask UPI growth`):**
- Full output: synthesis + per-group detail + methodology
- Sent immediately as a single (possibly multi-message) response in MeChat

**Cron (scheduled):**
- Same full output
- Plus a feedback prompt:
  ```
  ━━━━━━━━━━━━━━━━━━
  👍 *Like this?* → /cron keep <id> · set recurring
  👎 *Improve it?* → /cron feedback <id> "your feedback"
  ⏹ *Stop* → /cron pause <id>
  ```

#### Feedback Incorporation

When a cron job is `retry_pending` with feedback text:
- The feedback is passed to Step 5 (cross-group synthesis) as an additional instruction:
  ```
  The user gave this feedback on the previous run:
  "{feedback}"
  Adjust this synthesis accordingly.
  ```

### 3.5 Slash Commands

**No changes needed to the slash command logic.** The existing system works because:

1. The Go bridge directly spawns `wacmd.py` when it sees `/` messages (line 474-504 of
   `main.go`).
2. Hermes just ensures the bridge binary is running — which it does automatically via
   the supervisor.
3. The owner protection in `wacmd.py:390-407` already works — non-owner slash commands
   are silently dropped.
4. For slash commands typed in MeChat: the bridge spawns `wacmd.py`, which processes and
   sends a reply. Hermes's MeChat handler is NOT invoked for `/` messages (the bridge
   routes `/` to wacmd.py, not hermes_handler.py).

**One edge case:** If the user types `/ask UPI growth` in MeChat, this starts with `/`
so the bridge routes it to `wacmd.py`. But `wacmd.py` only handles known commands
(`/sotu`, `/pending`, `/stats`, `/recap`, `/eli5`, `/help`). Unknown commands are
silently ignored (line 429).

**Resolution:** The bridge should route `/ask`, `/cron`, and `/search` to
`hermes_handler.py` instead of `wacmd.py`. The routing logic in the bridge becomes:

```
if message starts with "/":
    if command is in ["/sotu", "/pending", "/stats", "/recap", "/eli5", "/help"]:
        spawn wacmd.py
    else if command starts with "/ask" or "/cron" or "/search":
        spawn hermes_handler.py
    else:
        ignore (unknown command)
else if chat is MeChat and sender is owner:
    spawn hermes_handler.py  (natural language)
```

### 3.6 Sender (`hermes_bot/sender.py`)

Reuses the same pattern as existing `wa_pull/sender.py`:
- `send_message(jid, text)` → POST to `http://localhost:8080/api/send`
- `send_to_mechat(text)` → resolves MeChat JID, calls `send_message`
- Retry logic: 3 attempts on ConnectionError
- Multi-message splitting: if text exceeds WhatsApp's ~4096 char limit, split at paragraph
  boundaries, prefix with `(1/3)`, `(2/3)`, `(3/3)`.

### 3.7 Database Layer (`hermes_bot/db.py`)

Extends patterns from `wa_pull/db.py`:

**Queries from existing (reuse):**
- `get_own_jid()` — detect owner
- `get_mechat_chat_jid()` — find MeChat LID-based JID
- `get_mechat_messages_since(since)` — poll new MeChat messages
- `get_non_archived_groups()` — list groups (from scanner.py)
- `get_chat_messages(chat_jid, days, limit)` — fetch messages with sender names

**New queries:**
- `get_non_archived_chats_all()` — groups + individuals (for broader search)
- `get_group_recent_activity(group_jid, days)` — message count + last activity
- `resolve_contact_name(jid)` — single name resolution

---

## 4. Data Flow Diagrams

### 4.1 MeChat Message Flow

```
User sends "what's the status on the Q3 product launch?" in MeChat
         │
         ▼
Go Bridge receives message via WhatsApp WebSocket
         │
         ├──► Writes to messages.db (always)
         │
         └──► Is this MeChat? Is sender = owner?
                │ yes
                ▼
              Spawns: python3 hermes_bot/mechat_handler.py <chat_jid> <sender_jid> <message>
                │
                ▼
              Handler loads session from store/session.json
                │
                ▼
              Continuity Check (LLM):
                Active session? "discussing product roadmap"
                Confidence: 0.85, continues: true
                │
                ▼
              Intent Router (LLM):
                intent: "question"
                query: "Q3 product launch status"
                needs_context: true
                context_scope: "all_groups"
                │
                ▼
              Hot-Word Deep Dive Pipeline:
                Scan 47 groups → 8 relevant → fetch → summarize → synthesize
                │
                ▼
              Format + Send to MeChat via POST :8080/api/send:
                🤖 *Q3 Product Launch · Status*
                🎯 Overall: ...
                📋 Key items: ...
                🔍 Methodology: ...
                │
                ▼
              Handler saves session to store/session.json, exits
```

### 4.2 Cron Job Flow

```
09:00 IST — Scheduler wakes
         │
         ▼
Query: cron_jobs WHERE next_run_at <= now()
  Finds: "UPI growth" · daily · 09:00
         │
         ▼
Hot-Word Deep Dive Pipeline:
  (same as one-shot, but with cron context)
         │
         ▼
Format + Send to MeChat:
  🤖 *Daily Brief · UPI Growth · 09:00*
  [synthesis + bullets + methodology]
  ━━━━━━━━━━━━━━━━━━
  👍 /cron keep upi_daily
  👎 /cron feedback upi_daily "..."
         │
         ▼
Update cron_jobs.last_run_at, next_run_at
Set status = "awaiting_feedback"
         │
         ▼
(Scheduler re-checks in 30s — nothing due)
```

### 4.3 Feedback Loop Flow

```
User: "👎 /cron feedback upi_daily too broad, only show UPI growth numbers, not marketing"
         │
         ▼
Hermes handler:
  Updates cron_jobs.feedback = "too broad, only show UPI growth numbers, not marketing"
  Sets status = "retry_pending"
  next_run_at = now + 10 min (retry soon)
         │
         ▼
Sends: ✅ *Feedback saved* · Next UPI growth summary will focus on growth numbers only.
         │
         ▼
10 min later — Scheduler runs:
  Detects retry_pending + feedback
  Runs deep dive with feedback injected into Step 5 prompt
         │
         ▼
Sends revised summary + feedback prompt again
         │
         ▼
User: "👍 /cron keep upi_daily"
         │
         ▼
Status → "active", freq stays "daily 09:00"
No more feedback gate (unless user opts in again)
```

---

## 5. Project Structure

```
master_project/
├── hermes_bot/                   # NEW: the unified bot
│   ├── __init__.py
│   ├── main.py                   # entry point: supervisor + cron scheduler launcher
│   ├── config.py                 # env vars + constants
│   ├── supervisor.py             # bridge lifecycle, QR relay, health check, crash recovery
│   ├── sender.py                 # WhatsApp message sender
│   ├── db.py                     # all SQLite queries (extended from wa_pull/db.py)
│   │
│   ├── mechat_handler.py         # CLI entry point invoked by Go bridge per MeChat message
│   │
│   ├── assistant/                # MeChat personal assistant logic
│   │   ├── __init__.py
│   │   ├── session.py            # conversation session + continuity state (JSON-persisted)
│   │   ├── continuity.py         # LLM-based continuity detection
│   │   ├── handler.py            # intent routing, dispatch
│   │   └── responder.py          # free-form question/statement responses
│   │
│   ├── cron/                     # hot-word deep dive system
│   │   ├── __init__.py
│   │   ├── scheduler.py          # APScheduler daemon (runs in main process)
│   │   ├── cron_store.py         # SQLite CRUD for cron jobs + run log
│   │   ├── searcher.py           # hot-word deep dive pipeline (5 steps)
│   │   ├── summarizer.py         # LLM summarization prompts
│   │   └── feedback.py           # feedback state machine
│   │
│   └── store/                    # hermes's own data (created at runtime)
│       ├── hermes.db             # cron_jobs, cron_run_log tables
│       └── session.json          # current conversation session state
│
├── components/                   # EXISTING (modified: wa_bridge gets MeChat trigger)
│   ├── wa_bridge/                # Go bridge — add MeChat owner detection + handler spawn
│   ├── wa_slash_commands/        # wacmd.py, engine.py, etc. — used as-is
│   └── wa_pull/                  # reference implementation, kept for compatibility
│
├── scripts/                      # NEW: start script
│   ├── hermes_start.sh           # ./hermes_start.sh → builds bridge → python3 hermes_bot/main.py
│   └── hermes_install.sh         # one-line install
│
└── docs/                         # documentation
    └── superpowers/
        └── specs/
            └── 2026-08-07-hermes-design.md  # THIS FILE
```

---

## 6. Environment Variables

```env
# Required
GEMINI_API_KEY=...

# Auto-detected (from whatsmeow_device table), but can override
OWNER_PHONE_NUMBER=91XXXXXXXXXX

# Bridge paths (defaults work out of the box)
MESSAGES_DB_PATH=components/wa_bridge/store/messages.db
WHATSAPP_DB_PATH=components/wa_bridge/store/whatsapp.db
WA_API_URL=http://localhost:8080

# Hermes-specific
HERMES_DB_PATH=hermes_bot/store/hermes.db
SESSION_FILE=hermes_bot/store/session.json
CONTINUITY_MODEL=gemini-3.1-flash-lite-preview
DEEP_DIVE_MODEL=gemini-3.1-flash-lite-preview
SESSION_TIMEOUT_MINUTES=60
MAX_GROUPS_PER_SEARCH=10
SEARCH_LOOKBACK_DAYS=14
SEARCH_MESSAGES_PER_GROUP=200

# Optional
CRON_ENABLED=1
MONITOR_ENABLED=0              # pulse monitor disabled by default (can enable later)
```

---

## 7. The `hermes start` Command

Single entry point for the user:

```bash
./scripts/hermes_start.sh
```

What it does:
1. If `components/wa_bridge/wa-bridge` binary doesn't exist, build it: `cd components/wa_bridge && go build -o wa-bridge .`
2. If `.env` doesn't exist, copy from `.env.example` and prompt for GEMINI_API_KEY.
3. Launch: `python3 hermes_bot/main.py`

Hermes `main.py`:
```python
def main():
    # 1. Load config, validate env vars
    # 2. Launch bridge via supervisor (handles QR display, pairing wait)
    # 3. Wait for bridge to be ready (:8080 accepts connections)
    # 4. Start cron scheduler as daemon thread
    #    (MeChat handler is NOT started here — it's invoked by the bridge per message)
    # 5. Send welcome message to MeChat
    # 6. Block on signal handler (SIGINT → graceful shutdown)
```

---

## 8. What We Are NOT Building (First Prototype)

Explicitly out of scope for the first working prototype:

- **Pulse monitor** — the real-time tag/mention/quick-succession detector. Can be
  added as opt-in later (code already exists in wa_pull).
- **Hourly bulletins** — the top-of-hour task summary. Not part of the personal
  assistant UX.
- **OKF bundle + persona** — the per-chat concept docs. Useful for context but
  adds complexity. The deep-dive system fetches raw messages directly.
- **Task extraction / action options (A/B/C)** — the task-tracking workflow.
  Separate concern from the assistant.
- **Web dashboard / API** — the ops FastAPI backend. Not needed for WhatsApp-only UX.
- **Nudge system** — the ops card-based nudge engine. Separate product.
- **Multi-user support** — single user, single WhatsApp account only.
- **Group replies** — Hermes only responds in MeChat. It reads groups but does not
  write to them (except via manual slash commands).

---

## 9. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM latency makes continuity check feel slow | User waits 2-4s for each message response | Use `gemini-3.1-flash-lite-preview` (fast). Show typing indicator: "..." sent immediately. |
| Deep dive on 47 groups is expensive (many LLM calls) | 30-60s total pipeline time | Send progress messages: "🔍 Scanning 47 groups...", "✅ Found 12 relevant · Summarizing..." |
| Gemini rate limits on batch group scoring | Pipeline stalls | Implement 1s delay between batch calls. Queue and retry on 429. |
| Bridge crash mid-deep-dive | Incomplete summary sent, bridge blocks on handler subprocess | Wrap handler in try/except. On failure: send "⚠️ Search interrupted · Try again". Handler always exits cleanly. Bridge monitors subprocess timeout (120s max). |
| Rapid messages during deep dive | Bridge spawns handler for msg 2 while handler for msg 1 is still running | Bridge queues subprocess invocations — only one handler runs at a time. WhatsApp messages are sequential anyway. |
| Session JSON corruption (concurrent writes) | Session state lost | Since bridge queues handlers sequentially, no concurrent writes happen. Atomic write: write to temp file, then rename. |
| MeChat LID changes (WhatsApp protocol update) | Bot can't find MeChat | Re-detect MeChat JID on startup. Fallback to `own_phone@s.whatsapp.net` pattern. |

---

## 10. Testing Strategy

### Unit Tests
- `test_supervisor.py` — bridge lifecycle mock, QR detection, health check polling
- `test_continuity.py` — mock LLM responses, verify decision matrix for all confidence levels
- `test_cron_store.py` — CRUD operations, state transitions
- `test_searcher.py` — pipeline steps with mock DB and mock LLM
- `test_handler.py` — intent routing with mock messages

### Integration Tests
- `test_e2e_mechat.py` — spin up bridge + hermes, send messages to MeChat via API, verify
  responses (requires live WhatsApp or mock bridge)
- `test_e2e_cron.py` — create cron, wait for trigger, verify summary sent
- `test_e2e_feedback.py` — full feedback loop: dislike → retry → like → keep

### Manual Test Checklist (First Prototype)
- [ ] `hermes start` with no pairing → QR code appears
- [ ] Scan QR → welcome message in MeChat
- [ ] Send "hello" → Hermes responds, session created
- [ ] Send "what about the project timeline?" → continuity check fires, continues session
- [ ] Send "what's the weather?" → new topic detected, ask or start fresh
- [ ] Send "/ask UPI growth" → deep dive runs, summary delivered
- [ ] Send "/cron add 'UPI growth' daily 09:00" → cron created, confirmed
- [ ] Wait for cron to fire → summary delivered with feedback prompt
- [ ] Send "/cron feedback <id> too broad" → retry triggers, improved summary
- [ ] Send "/sotu" in a group → slash command works (via bridge)
- [ ] Send "/sotu" in MeChat → slash command works (via bridge)
- [ ] Slash command from non-owner → silently ignored
- [ ] Bridge crash → supervisor restarts, status message sent

---

## 11. Implementation Plan (High-Level)

This spec covers the design. The implementation will be broken into these phases:

| Phase | What | Dependencies |
|-------|------|-------------|
| **P0: Scaffold** | Project structure, config, sender, db.py, supervisor.py with bridge lifecycle + QR relay | None |
| **P1: Bridge MeChat Trigger** | Modify Go bridge `handleMessage()` to detect MeChat + owner and spawn `hermes_handler.py` | P0 |
| **P2: MeChat Handler** | `mechat_handler.py` CLI script: load session, filter own messages, basic response | P1 |
| **P3: Continuity** | Session manager (JSON-persisted), continuity LLM call, decision matrix, topic tracking | P2 |
| **P4: Intent Router** | Intent detection LLM call, dispatch to handler stubs | P2 |
| **P5: Hot-Word Pipeline** | `searcher.py` 5-step pipeline, per-group summary, synthesis, methodology footer | P0, DB access |
| **P6: Cron System** | `cron_store.py`, `scheduler.py`, feedback state machine, `/cron` commands | P0, P5 |
| **P7: Free-Form Assistant** | Responder for non-search questions, context-aware responses | P3, P4 |
| **P8: Slash Integration** | Ensure bridge + wacmd.py work, no double-response edge case | P0 |
| **P9: Start Script & Polish** | `hermes_start.sh`, `.env` setup, welcome flow, error messages, SIGINT handling | All above |

**First deliverable (P0–P5):** User can start Hermes, scan QR, send messages in MeChat
and get responses. Conversation continuity works. One-shot `/ask` deep dives work.
Slash commands work in groups.

**Second deliverable (P6–P9):** Cron jobs with feedback loop. Free-form questions.
Polished start script. Complete v1.

---

## 12. Open Questions

1. **Should the continuity check be a blocking LLM call or async?** For v1, blocking
   is fine — the 2-4s latency is acceptable for a MeChat conversation. Can optimize
   later with streaming or pre-computation.

2. **What happens when the user is typing a long message and Hermes sends a cron
   summary at the same time?** WhatsApp handles this naturally — both messages appear
   in order. Hermes should avoid sending during an active conversation session
   (defer cron by 2 minutes if user has been active in the last 60 seconds).

3. **Should cron summaries include messages from direct/individual chats too?** For
   v1, groups only (`%@g.us`). Individual chats are private conversations where
   relevance filtering is less meaningful. Can add later.

4. **How many cron jobs can a user have?** v1: max 5 active cron jobs. Keeps the
   scheduler simple and prevents notification fatigue.
