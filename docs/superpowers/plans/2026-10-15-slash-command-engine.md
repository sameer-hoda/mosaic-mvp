# Slash command engine — short plan (Worker C)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (native; single worker, small scope).
> Implement task-by-task below with TDD: failing test → minimal code → green → commit.

**Goal:** Private `/catchup /pending /sotu /eli5` answers to self-chat, with best-effort
invoke-delete, `!` + `yes` gated public posts, and per-chat `/mute` + `/unmute`.

**Architecture:** New `hermes_bot/slash.py` engine with injectable deps (fetch/send/delete/clock)
so tests run offline; `hermes_bot/slash_format.py` for WA templates; thin best-effort hook
in `hermes_bot/brain.py` `POST /event`. No bridge or supervisor changes.

**Tech Stack:** Python 3 stdlib + `requests` (already used by `sender.py`); pytest.

**Spec:** Task block in dispatch `ctx_62d037c6d9d6` (no separate spec doc).

## Global Constraints

- No group post without `yes` within 5 min; default stays private.
- Secrets via env, never in files.
- Do not rework Worker A's bridge event hook (`components/wa_bridge/main.go`) or `supervisord.conf`.
- Tests run via `python3 -m pytest tests/ -q`.
- No push to `main`; commit on worktree branch.

## Review Focus

- `!` public flow quoting exact text plus chat name, sent only after `yes` in window.
- `yes` arriving after expiry, or from a non-owner, must not trigger a group post.
- Muted chats stay silent for analysis commands but still accept `/unmute`.
- Delete-path failure (no endpoint / timeout) is logged, never raised.

---

### Task 1: Parsing + owner gate + private routing

**Files:**
- Create: `hermes_bot/slash.py`
- Create: `hermes_bot/slash_format.py`
- Test: `tests/test_slash.py`

**Interfaces:**
- Consumes: `hermes_bot.db.get_mechat_chat_jid()`, `hermes_bot.db.is_message_from_owner()`,
  env `MOSAIC_SELF_JID` fallback (TODO: remove once Worker B resolution confirmed).
- Produces: `parse_command(text: str) -> dict | None` with keys
  `{name: str, public: bool, arg: str}`;
  `Engine.handle_event(chat_jid, sender_jid, content, msg_id=None) -> str`
  returning outcome labels (`"private-sent"`, `"rejected-not-owner"`, `"ignored"`, …).

- [ ] **Step 1: Write failing tests** in `tests/test_slash.py`: parsing per command
  (`/catchup`, `/pending`, `/sotu`, `/eli5 <topic>`, `/mute`, `/unmute`, `/catchup!` public flag,
  unknown → `None`); routing test (fake deps: answer goes to self JID only, group sender untouched);
  owner-rejection test (non-owner → no sends).
- [ ] **Step 2: Run** `python3 -m pytest tests/test_slash.py -q` → FAIL (`slash` module missing).
- [ ] **Step 3: Implement** `parse_command` + `Engine` with owner check, self-chat resolution
  (`db.get_mechat_chat_jid()` → `MOSAIC_SELF_JID` fallback), deterministic local analysis
  formatting via `slash_format.py`. Delete attempted best-effort (see Task 2).
- [ ] **Step 4: Run** full suite `python3 -m pytest tests/ -q` → green.

### Task 2: Best-effort invoke-delete (2 s)

**Files:**
- Modify: `hermes_bot/slash.py`
- Test: `tests/test_slash.py`

**Interfaces:**
- Produces: `Engine(deleter=...)`; default deleter POSTs `{BRIDGE_URL}/api/delete`
  with 2 s timeout and logs `slash: deletion impossible (...)` on any failure, never raises.

- [ ] **Step 1: Add failing tests**: deleter called on command events; deleter raising →
  command still served, failure logged (caplog), no exception.
- [ ] **Step 2: Run** new tests → FAIL.
- [ ] **Step 3: Implement** delete call wrapped in try/except with 2 s timeout.
- [ ] **Step 4: Run** full suite → green.

### Task 3: `!` public-post confirm state machine (5 min `yes` gate)

**Files:**
- Modify: `hermes_bot/slash.py`, `hermes_bot/slash_format.py`
- Test: `tests/test_slash.py`

**Interfaces:**
- Produces: `ConfirmStore.request(chat_jid, text, chat_name, now)`,
  `ConfirmStore.confirm(chat_jid, now) -> dict | None` (expiry 300 s, one-shot);
  preview format includes exact text plus chat name.

- [ ] **Step 1: Add failing tests**: `/catchup!` → preview to self-chat, zero group sends;
  `yes` in window → exactly one group send with analysis text; `yes` after 5 min → no send;
  plain analysis (no `!`) never touches group sender even after `yes`.
- [ ] **Step 2: Run** new tests → FAIL.
- [ ] **Step 3: Implement** `ConfirmStore` (injectable clock) + `yes` handling on the
  self-chat/originating-chat path with owner re-check.
- [ ] **Step 4: Run** full suite → green.

### Task 4: `/mute` + `/unmute` per chat + brain hookup

**Files:**
- Modify: `hermes_bot/slash.py`, `hermes_bot/brain.py`
- Test: `tests/test_slash.py`

**Interfaces:**
- Produces: `MuteStore.is_muted(chat_jid)`, `.mute(chat_jid)`, `.unmute(chat_jid)`
  (JSON file under `STORE_DIR`, in-memory fallback); brain `POST /event` calls
  `Engine.handle_event` best-effort inside try/except (hook shape unchanged).

- [ ] **Step 1: Add failing tests**: `/mute` → later `/catchup` in same chat gets
  `"muted"` outcome with no sends; other chats unaffected; `/unmute` re-enables;
  mute/unmute from non-owner rejected.
- [ ] **Step 2: Run** new tests → FAIL.
- [ ] **Step 3: Implement** `MuteStore` + brain hookup (import-guarded, never breaks `/event` ack).
- [ ] **Step 4: Run** full suite → green, then commit branch (no push) and `worker_done`.
