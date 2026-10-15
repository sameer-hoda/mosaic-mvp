# Self-chat onboarding (Worker B) — short plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pairing-less self-chat onboarding: setup page, PN+LID identity, welcome flow, zero groups.

**Architecture:** Port v4 `mechat/identity.py` freshest-wins PN+LID resolution into `hermes_bot/identity.py`; rewire `db.py` owner checks to it; new `hermes_bot/onboarding.py` owns the guarded welcome send (dry-run unless `MOSAIC_LIVE=1`); rewrite `wizard.html` to 3-item setup view + connected view; Go auto-pairs self-chat on connect.

**Tech Stack:** Python stdlib + sqlite3 + pytest; Go bridge (whatsmeow, stdlib http).

**Spec:** dispatch brief task_f9636053c4a5 (setup page / identity / no-group / welcome / Superpowers TDD+YAGNI).

## Global Constraints

- Never create, address, or send to any group or contact; all sends go to the resolved self-chat JID only.
- Secrets via env, never in files.
- Do not rework the bridge event hook (`postBrainEvent`) or supervisor config (`supervisord.conf`, `supervisor.py`).
- Do not push to `main`. Tests run via `python3 -m pytest tests/ -q`.

## Review Focus

(none beyond the tasks — every spec clause has a pinned test below.)

---

### Task 1: Identity module (port v4 freshest-wins PN+LID)

**Files:**
- Create: `hermes_bot/identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Consumes: session DB `whatsmeow_device(jid, lid)`, `whatsmeow_lid_map(lid, pn)`; messages DB `messages(chat_jid, timestamp, is_from_me)`.
- Produces: `own_identity(session_db) -> (phone, lid, source)`; `is_owner_jid(jid, session_db) -> bool`; `resolve_self_chat(messages_db, session_db) -> (jid, how)`.

- [ ] **Step 1: Write failing test.** Fixture session DB with device row `919876543210:16@s.whatsapp.net` + lid `219541632213229:37@lid`, lid_map row, and messages in both `91…@s.whatsapp.net` and `…@lid` forms. Assert PN form and LID form both `is_owner_jid`, and `resolve_self_chat` returns the freshest (MAX timestamp) of the two.
- [ ] **Step 2: Run it.** Run: `python3 -m pytest tests/test_identity.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `own_identity / is_owner_jid / resolve_self_chat` in `hermes_bot/identity.py` (port v4: `_split_user` strips `:device`/`@server`; lid_map checked both directions; freshest-wins via MAX(timestamp)).
- [ ] **Step 4: Rewire** `hermes_bot/db.py` `is_message_from_owner` + `get_mechat_chat_jid` to delegate to identity (same signatures).
- [ ] **Step 5: Run.** `python3 -m pytest tests/ -q`. Expected: PASS.

### Task 2: Onboarding module — no group ever, guarded welcome, dry-run default

**Files:**
- Create: `hermes_bot/onboarding.py`
- Modify: `hermes_bot/main.py` (welcome block delegates to onboarding), `hermes_bot/sender.py` (reject group JIDs in `enqueue_message`)
- Test: `tests/test_onboarding.py`

**Interfaces:**
- Consumes: `identity.resolve_self_chat`; `sender.enqueue_message`.
- Produces: `WELCOME_TEXT`; `create_group(*a, **k)` stub raising; `send_welcome(jid=None) -> dict`; `run_onboarding() -> dict`.

- [ ] **Step 1: Write failing tests.** (a) AST scan of `onboarding.py`: zero call-nodes matching group-create names. (b) With fixture DBs + `MOSAIC_LIVE=1` + mocked enqueue: welcome sent exactly once to the resolved self-chat JID. (c) Without `MOSAIC_LIVE`: dry-run, zero enqueues. (d) Group JID to `send_welcome` raises; `create_group` stub raises; `enqueue_message` rejects `@g.us`.
- [ ] **Step 2: Run it.** Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `hermes_bot/onboarding.py` (JID guard: must equal resolved self-chat, never `@g.us`; dry-run unless `MOSAIC_LIVE=1`); add group guard to `sender.enqueue_message`; point `main.py` READY-transition welcome at `onboarding.send_welcome`.
- [ ] **Step 4: Run.** `python3 -m pytest tests/ -q`. Expected: PASS.

### Task 3: Setup page — three things, then connected-line + Unlink

**Files:**
- Modify: `components/wa_bridge/wizard.html`
- Modify (tiny, additive): `components/wa_bridge/main.go` — auto-pair self-chat on connect; add `last_message_at` to `/setup/state`
- Test: `tests/test_setup_page.py`

**Interfaces:**
- Consumes: existing `/setup/state` (+ new `last_message_at`), `/setup/reset` for Unlink.
- Produces: `#view-setup` with `#bridge-status`, `#qr-img` (2s auto-refresh), `#gemini-tick`; `#view-connected` with `#connected-line` (`Connected as +… · last message N min ago`) + `#unlink-btn`. No pairing-code/group UI.

- [ ] **Step 1: Write failing test.** Parse `wizard.html`: assert the three setup IDs + auto-refresh interval exist; assert connected-line format + unlink exist; assert no `pairing-code` / `MOSAIC-` / `g.us` UI references.
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Rewrite** `wizard.html` (keep console login gate; drop key-entry/pairing/dashboard screens); Go: on connect set `MeChatJID=<PN>@s.whatsapp.net` + READY, expose `last_message_at`.
- [ ] **Step 4: Verify.** `python3 -m pytest tests/ -q` PASS + `cd components/wa_bridge && go build ./...` compiles.
- [ ] **Step 5: Commit** on `mosaic-selfchat` (no push), send `worker_done` once.
