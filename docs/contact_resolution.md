# Contact Resolution — The Complete Guide

> **Why this matters:** WhatsApp never exposes real names in its raw protocol. Every
> bot script in this repo (wa-pull, slash-commands, channel_manager, wa_productivity,
> wa_brain, master_tracker) sees *identifiers* — JIDs, LIDs, phone numbers — and has to
> translate them back to human names before anything is displayed, summarized, or sent.
> The resolution logic is duplicated (with slight variations) in at least 6 modules.
> This document is the single source of truth for how it all works.

---

## 1. Identifier Types You Will Encounter

| Type | Format | Example | Where it comes from |
|------|--------|---------|---------------------|
| **JID (user)** | `<phone>@s.whatsapp.net` | `91XXXXXXXXXX@s.whatsapp.net` | `messages.sender`, `whatsmeow_contacts.their_jid` |
| **JID (device)** | `<phone>:<id>@s.whatsapp.net` | `919876543210:16@s.whatsapp.net` | `whatsmeow_device.jid` (owner row) |
| **LID (Local ID)** | `<big number>@lid` or bare digits | `219541632213229@lid` | LLM output, newer WhatsApp protocol, MeChat `chat_jid` |
| **LID map key** | `<lid>:<id>` (colon suffix) | `219541632213229:37@lid` | `whatsmeow_lid_map.lid` |
| **Phone number** | digits, country-code first | `91XXXXXXXXXX` | parsed from JID / user input |
| **Group JID** | `<id>@g.us` | `120363405680258025@g.us` | `messages.chat_jid`, `chats.jid` |
| **Status broadcast** | special | `status@broadcast` | must be excluded from chat lists |

**LLM leakage:** The Gemini/LLM layer sometimes emits raw identifiers like `@123456789012`
in task summaries and drafted reply options. There is a regex cleaner for this
(`wa-pull/contact_resolution.py:79` — `@(\d{10,})`).

---

## 2. The Two Databases (produced by the Go bridge)

| DB | Tables | Role |
|----|--------|------|
| **`whatsapp.db`** | `whatsmeow_contacts`, `whatsmeow_lid_map`, `whatsmeow_message_secrets`, `whatsmeow_device`, `whatsmeow_chat_settings` | Session, contacts, identity mapping |
| **`messages.db`** | `messages`, `chats` | Message history + chat metadata |

The Python layer always does `ATTACH DATABASE whatsapp.db AS wa` and joins across.

### Key tables

- **`whatsmeow_contacts`** — `their_jid`, `push_name`, `full_name`, `first_name`, `business_name`. The name source of last resort. JIDs can be stored as `@s.whatsapp.net` **or** `@lid` form — check both.
- **`whatsmeow_lid_map`** — `lid` → `pn` (phone number). The critical LID→phone bridge. Both columns may carry a `:<id>` device suffix.
- **`whatsmeow_message_secrets`** — `message_id`, `chat_jid`, `sender_jid`. **Group chats only store the group JID in `messages.sender`** — this table is the only way to get the actual sender's JID inside a group.
- **`whatsmeow_device`** — the owner's device row (`jid`, `lid`). Used to detect the owner (MeChat).
- **`whatsmeow_chat_settings`** — `chat_jid`, `archived`. Filter for non-archived chats.
- **`chats`** — `jid`, `name`, `last_message_time`. Chat-level name (group names, saved contact names).
- **`messages`** — `id`, `chat_jid`, `sender`, `content`, `timestamp`, `is_from_me`, `media_type`, `filename`, `url`.

---

## 3. The Core Resolution Algorithm

Everything reduces to: **get a JID, then look up `whatsmeow_contacts`.**

```
         ┌─────────────┐
         │  identifier │
         └──────┬──────┘
                │
     ┌──────────┼───────────────┐
     ▼          ▼               ▼
  digits     "@lid"/JID     group JID
     │          │               │
     ▼          ▼               │
 lid_map ──► pn ──► @s.whatsapp.net    └──► detect & label "group"
     │  (no pn? treat digits as phone)
     ▼
  whatsmeow_contacts
     COALESCE(push_name, full_name, first_name, business_name)
     │
     ▼
  name  ── no name ──► fallback: phone part of JID, then raw input
```

### Case 1 — You have a **LID** (most common for AI output)

```sql
-- 1. LID → phone number
SELECT pn FROM whatsmeow_lid_map WHERE lid = '219541632213229';
-- 2. JID = pn + '@s.whatsapp.net'
-- 3. Name
SELECT COALESCE(push_name, full_name, first_name, business_name)
FROM whatsmeow_contacts WHERE their_jid = '91XXXXXXXXXX@s.whatsapp.net';
```

**Critical fallback:** if the digits are *not* a valid LID, treat them as a phone number
and build the JID directly (`<digits>@s.whatsapp.net`). This is what makes bare phone
numbers work.

### Case 2 — You have a **phone number**

Jump straight to step 3 with `<number>@s.whatsapp.net`.

### Case 3 — You have a **JID**

Query `whatsmeow_contacts` directly. Also try the `@lid` variant of the JID
(`their_jid = '<number>@lid'`) — contacts can be keyed on either form
(see `channel_manager/contacts.py:112-121`).

### Case 4 — **Group chat sender**

In a group, `messages.sender` is the **group JID**. To find who actually spoke:

```sql
SELECT c.*
FROM messages m
LEFT JOIN wa.whatsmeow_message_secrets ms ON m.id = ms.message_id AND m.chat_jid = ms.chat_jid
LEFT JOIN wa.whatsmeow_contacts c ON ms.sender_jid = c.their_jid
WHERE m.chat_jid = '<group>@g.us';
```

(`wa-pull/db.py:162-198` is the canonical implementation.)

### Name priority

```sql
COALESCE(push_name, full_name, first_name, business_name)
```

- `push_name` — the user's self-set WhatsApp display name (usually most reliable, always populated for active users).
- `full_name` / `first_name` — from the phone's contact book.
- `business_name` — for business accounts.

⚠️ **Inconsistency alert:** `channel_manager/contacts.py` orders it
`full_name > push_name > first_name > business_name`. Both work; be aware when
comparing outputs across components.

### Fallback chain when no name found

1. The `chats.name` for that JID (`integration.py:88`).
2. The phone part of the JID (`jid.split('@')[0]`).
3. The raw input / `"Unknown"`.

---

## 4. Special Cases Specific to This Codebase

### 4.1 Owner / MeChat detection

The owner's phone is derived from `whatsmeow_device.jid`:

```python
# wa-pull/db.py get_own_jid()
SELECT jid FROM wa.whatsmeow_device LIMIT 1       # "919876543210:16@s.whatsapp.net"
phone = jid.split(":")[0]                          # "919876543210"
return f"{phone}@s.whatsapp.net"
```

MeChat (the chat with yourself) is tricky: new WhatsApp protocol uses a **LID-based
chat JID**, so `get_mechat_chat_jid()` resolves it via `whatsmeow_lid_map` where
`pn = own_phone`, falling back to the device `lid` column, then to the phone JID.

### 4.2 Group JID heuristic

`channel_manager/contacts.py` treats 17–18 digit numbers starting with `120` (or any
`@g.us`) as groups and labels them `"group"` instead of resolving:

```python
def _is_group_jid(v):  # "@g.us" in v  OR  v.isdigit() and len>=17 and v.startswith("120")
```

### 4.3 Reverse lookup — name → JID

`wa_productivity/utils/contact_helper.py` provides the inverse direction:

```python
find_jid_by_name("Rahul")      # LIKE '%Rahul%' across all 4 name fields → their_jid
get_all_identifiers(jid)       # returns {jid, phone, lid} set for a person
```

Used for whitelisting and "send to person by name".

### 4.4 `@lid` suffix hygiene

- Strip `@lid` before resolving (`identifier.replace('@lid','')`).
- Strip the `:<device>` suffix on LID map keys: `'219541632213229:37@lid' → '219541632213229'`.
- Do **not** strip `@s.whatsapp.net` — it's the lookup key in `whatsmeow_contacts`.

### 4.5 OKF bundle file naming

`wa-pull/okf_builder.py` persists per-contact knowledge docs as
`okf_bundle/{contacts|groups}/{safe_name}_{jid_hash}.md`. The filename embeds a
human-readable slug (e.g. `Abhinav_Cred_8866.md`) plus a `%10000` hash of the JID for
uniqueness. Unresolved LIDs end up as `219541632213229_5436.md` — a visible symptom of
resolution gaps.

---

## 5. Where Each Implementation Lives

| Module | Path | Strengths |
|--------|------|-----------|
| **wa-pull (canonical)** | `components/wa_pull/contact_resolution.py` | Cached singleton, `resolve_text()` regex cleaner, read-only DB, `resolve_contact()` used by handlers + monitor |
| **wa-bot DB layer** | `components/wa_pull/db.py` | SQL joins across attached DBs; sender names in group chats; MeChat + owner detection |
| **wa-productivity (legacy)** | `components/wa_productivity/contact_resolution.py` | Same core logic, constructor takes DB path |
| **wa-productivity helper** | `components/wa_productivity/utils/contact_helper.py` | Name→JID reverse lookup, `get_all_identifiers()`, attaches DBs |
| **channel_manager** | `components/channel_manager/contacts.py` | Group-JID heuristic, LID-variant contact lookup, module-level cache |
| **wa-slash bridge** | `components/wa_slash_commands/bridge/integration.py` | Group member activity with names via SQL join |
| **wa_brain** | `components/wa_brain/contact_resolution.py` | Small variant for brain writer |
| **Original guide** | `docs/archive_docs/contact_resolution_guide.md` | Canonical narrative (LID/phone/JID cases) |

All seven follow the same LID→lid_map→JID→contacts flow. The differences are only in
caching, fallback order, and extra reverse-lookup helpers.

---

## 6. When Contact Resolution Is Invoked

1. **Drafting reply options / task summaries** — `handlers.py` calls `resolve_text()` so the LLM's `@<lid>` tokens become names.
2. **Pulse monitor alerts** — `monitor.py:285` resolves the chat name before rendering the alert.
3. **Chat/sender display** — `db.get_chat_messages()` resolves every sender via the message-secrets join.
4. **OKF + persona builds** — `okf_builder.py` names every chat doc.
5. **Owner detection & setup** — `wa-slash-commands/setup.py` reads `whatsmeow_device.jid` to auto-fill the owner phone.
6. **Group analysis / follow-ups** — `integration.py` names group members for `send 21 A/B/C` style replies.

---

## 7. Gotchas & Debugging

- **LID map keys carry device suffixes** — always `split(':')[0]` before matching.
- **Contacts may be keyed by `@lid` JID, not `@s.whatsapp.net`** — query both.
- **Group `sender` is the group, not the person** — never resolve group senders without the message-secrets join.
- **Don't resolve `status@broadcast`** — always exclude it.
- **Read-only connections** — use `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` so the bot never locks the bridge's live DBs.
- **DB paths** — wa-pull reads `store/whatsapp.db` + `store/messages.db` (env-overridable via `WHATSAPP_DB_PATH` / `MESSAGES_DB_PATH`). Older code points at `whatsapp-mcp/whatsapp-bridge/store/`.
- **Timestamps** — DB stores `"2026-07-23 14:55:48+05:30"`; format the comparison string exactly, in IST, or range queries silently fail.

### Quick debug queries

```sql
-- Who is this LID?
SELECT lid, pn FROM whatsmeow_lid_map WHERE lid LIKE '219541632213229%';

-- Every name we have for one person
SELECT their_jid, push_name, full_name, first_name, business_name
FROM whatsmeow_contacts
WHERE their_jid LIKE '%967151186%' OR their_jid LIKE '%541632213229%';

-- Actual senders inside a group
SELECT DISTINCT ms.sender_jid, COALESCE(c.push_name, c.full_name)
FROM messages m
JOIN wa.whatsmeow_message_secrets ms ON m.id = ms.message_id
LEFT JOIN wa.whatsmeow_contacts c ON ms.sender_jid = c.their_jid
WHERE m.chat_jid = '<group>@g.us' LIMIT 20;
```

---

## 8. Reference: the canonical SQL snippet

```sql
SELECT COALESCE(push_name, full_name, first_name, business_name) AS display_name
FROM whatsmeow_contacts
WHERE their_jid = ?;
```
