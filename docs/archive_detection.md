# Archive Detection — The Complete Guide

> **Why this matters:** Every bot in this repo needs to skip archived WhatsApp chats so
> that stale/abandoned conversations don't pollute task extraction, momentum updates,
> knowledge graphs, or CRM syncs. The archive flag is stored in one canonical table —
> but the query pattern is duplicated across 10+ modules with subtle variations.

---

## 1. The Data Source

### Table: `whatsmeow_chat_settings` (in `whatsapp.db`)

| Column       | Type    | Default | Meaning |
|-------------|---------|---------|---------|
| `our_jid`   | TEXT    | —       | Our device JID (PK, FK → `whatsmeow_device.jid`) |
| `chat_jid`  | TEXT    | —       | The chat's JID (PK) |
| `muted_until` | BIGINT | `0`    | Unix timestamp until muted |
| `pinned`    | BOOLEAN | `false` | Whether the chat is pinned |
| `archived`  | BOOLEAN | `false` | **`1` = archived, `0` = active** |

```sql
CREATE TABLE whatsmeow_chat_settings (
    our_jid       TEXT,
    chat_jid      TEXT,
    muted_until   BIGINT  NOT NULL DEFAULT 0,
    pinned        BOOLEAN NOT NULL DEFAULT false,
    archived      BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (our_jid, chat_jid),
    FOREIGN KEY (our_jid) REFERENCES whatsmeow_device(jid) ON DELETE CASCADE ON UPDATE CASCADE
);
```

### How it gets populated

The Go bridge (`whatsmeow`) writes this table automatically when syncing chat preferences
from WhatsApp servers. When you archive a chat in WhatsApp, the bridge updates the
`archived` column to `1`. No manual or Python-side writes are needed.

---

## 2. The Canonical Filter Pattern

Every module uses the same SQL pattern, with minor column-name differences:

```sql
WHERE (cs.archived IS NULL OR cs.archived = 0)
```

### Why `IS NULL OR = 0` and not just `= 0`?

The `whatsmeow_chat_settings` table only has rows for chats where the user has *explicitly*
changed a setting. A chat that was never archived, never pinned, and never muted **has no
row at all** in this table. A `LEFT JOIN` returns `NULL` for `cs.archived` in those cases.

**The mental model:**

| Situation | `cs.archived` value | Filter result |
|-----------|-------------------|---------------|
| Chat was archived | `1` | ❌ excluded |
| Chat was unarchived, or never touched | `0` | ✅ included |
| No row exists (never modified) | `NULL` | ✅ included |

⚠️ **Gotcha:** If you write `WHERE cs.archived = 0` (without the NULL check), you will
silently drop every chat that the user has never explicitly archived/unarchived. This
is the #1 bug to watch for.

---

## 3. Two Joining Strategies

There are two ways the filter is attached, depending on the query's purpose:

### Strategy A — Filtering chats (list queries)

When you want to enumerate chats, join on `chat_jid`:

```sql
FROM chats ch
LEFT JOIN wa.whatsmeow_chat_settings cs ON ch.jid = cs.chat_jid
WHERE (cs.archived IS NULL OR cs.archived = 0)
```

**Used by:** `scanner.py`, `wa_pull/db.py`, `taskdog-backend/models/database.py`,
`wa_productivity/generate_task_data.py`, `wa_productivity/master_context_builder.py`

### Strategy B — Filtering messages (message-level queries)

When you want to get messages but exclude archived chats, join on the message's
`chat_jid`:

```sql
FROM messages m
LEFT JOIN wa.whatsmeow_chat_settings cs ON m.chat_jid = cs.chat_jid
WHERE (cs.archived IS NULL OR cs.archived = 0)
```

**Used by:** `momentum_update/` scripts (all v2–v6), `generate_knowledge_graph.py`,
`daily_crm_sync.py`, `context_enrich/product_issues_pay_online/enrich_product_issues.py`,
`wiki_system/src/group_wiki.py`

---

## 4. Full Filter: All Exclusions Combined

Most queries combine archived filtering with other exclusions:

```sql
WHERE (cs.archived IS NULL OR cs.archived = 0)
  AND ch.jid != 'status@broadcast'              -- never include status
  AND (ch.jid LIKE '%@g.us'                     -- groups
       OR ch.jid LIKE '%@s.whatsapp.net'        -- individuals
       OR ch.jid LIKE '%@lid')                  -- LID-based chats (MeChat)
```

**Notes on JID filtering:**
- `status@broadcast` — always excluded (status updates, not chats).
- `%@g.us` — group chats.
- `%@s.whatsapp.net` — 1:1 conversations.
- `%@lid` — LID-based chats. `wa_pull/db.py` includes this to capture MeChat
  (the chat with yourself, which newer WhatsApp protocol stores with an `@lid` JID).
  Most other queries omit this and may silently miss MeChat messages.

---

## 5. Where Each Implementation Lives

| Module | Path | What it filters | Key detail |
|--------|------|----------------|------------|
| **wa-slash scanner** (canonical groups) | `wa_slash_commands/ops/scanner.py:51` | Groups only (`%@g.us`) | `list_non_archived_groups()` |
| **wa-pull OKF** | `wa_pull/db.py:119` | All chats (incl. `@lid`) | `get_non_archived_chats(min_messages=3)` |
| **taskdog-backend** | `taskdog-backend/models/database.py:292` | Groups + individuals (90-day active) | Chat search / JID resolution |
| **wa-productivity gen** | `wa_productivity/generate_task_data.py:55` | All (25-day active) | Simple cutoff-based |
| **wa-productivity builder** | `wa_productivity/master_context_builder.py:34` | All | Class method, same pattern |
| **momentum v2–v6** | `momentum_update/generate_momentum_update_simple_v*.py` | Messages-level filter | 14-day lookback |
| **knowledge graph** | `momentum_update/generate_knowledge_graph.py:62` | Messages-level filter | 14-day lookback |
| **CRM sync** | `momentum_update/daily_crm_sync.py:71` | Messages-level filter | — |
| **Wiki system** | `wiki_system/src/group_wiki.py:103` | Groups only | — |
| **Monthly theme** | `momentum_update/generate_theme_monthly_update.py:53` | Messages-level filter | — |
| **Context enrich** | `momentum_update/context_enrich/product_issues_pay_online/enrich_product_issues.py:194` | Messages-level filter | — |
| **Hourly scanner (legacy)** | `all_docs/archive_docs/pending_threads_analyzer.py:49` | Groups + individuals | `wcs.archived = 0` (no NULL check!) |

### The buggy one

The legacy `pending_threads_analyzer.py` and `task_brief_generator.py` in
`all_docs/archive_docs/` write `WHERE wcs.archived = 0` — omitting the `IS NULL` check.
These will silently drop every chat where the user has never toggled the archive setting.

---

## 6. Group-Only vs. All-Chats

Some consumers only care about groups; others want every active chat:

| Consumer | Scope | JID filter |
|----------|-------|-------------|
| `scanner.py` | Groups only | `WHERE ch.jid LIKE '%@g.us'` |
| `wiki_system/group_wiki.py` | Groups only | `WHERE ch.jid LIKE '%@g.us'` |
| `wa_pull/db.py` | All chats | `%@g.us OR %@s.whatsapp.net OR %@lid` |
| `taskdog-backend` | Groups + individuals | `%@g.us OR %@s.whatsapp.net` |
| All momentum_update scripts | Groups + individuals | `%@g.us OR %@s.whatsapp.net` |

---

## 7. Quick Debug Queries

```sql
-- Find every archived chat (attach whatsapp.db first)
SELECT cs.chat_jid, ch.name, cs.archived, cs.muted_until, cs.pinned
FROM wa.whatsmeow_chat_settings cs
LEFT JOIN chats ch ON cs.chat_jid = ch.jid
WHERE cs.archived = 1;

-- Find all chats with NO settings row (NULL archived — silently included by correct filter)
SELECT ch.jid, ch.name
FROM chats ch
LEFT JOIN wa.whatsmeow_chat_settings cs ON ch.jid = cs.chat_jid
WHERE cs.chat_jid IS NULL;

-- Count archived vs non-archived vs no-settings
SELECT
  CASE
    WHEN cs.archived = 1 THEN 'archived'
    WHEN cs.archived = 0 THEN 'active'
    ELSE 'no settings row'
  END AS archive_status,
  COUNT(*) AS chat_count
FROM chats ch
LEFT JOIN wa.whatsmeow_chat_settings cs ON ch.jid = cs.chat_jid
WHERE ch.jid != 'status@broadcast'
GROUP BY archive_status;

-- Check a specific chat's archive status
SELECT ch.name, cs.archived, cs.chat_jid
FROM chats ch
LEFT JOIN wa.whatsmeow_chat_settings cs ON ch.jid = cs.chat_jid
WHERE ch.jid = '120363405680258025@g.us';
```

---

## 8. How Archived Chats Differ from Muted Chats

These are separate concepts, both stored in `whatsmeow_chat_settings`:

| Setting | Column | Meaning | Effect on bots |
|---------|--------|---------|---------------|
| Archived | `archived` | Chat hidden from main inbox | **Excluded** from all processing |
| Muted | `muted_until` | Notifications silenced (Unix timestamp) | **NOT excluded** — bots still process muted chats |

A chat can be muted but not archived (still processed), or archived but not muted
(hidden + silent). There is no standard filter for muted chats anywhere in the codebase
— the archived flag is the only exclusion gate.

---

## 9. Canonical Python Snippet

```python
import sqlite3
from pathlib import Path

MESSAGES_DB_PATH = Path("store/messages.db")
WHATSAPP_DB_PATH = Path("store/whatsapp.db")

def get_non_archived_chats():
    """Return (jid, name) for every non-archived chat."""
    conn = sqlite3.connect(str(MESSAGES_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH DATABASE '{WHATSAPP_DB_PATH}' AS wa")

    rows = conn.execute("""
        SELECT ch.jid, ch.name
        FROM chats ch
        LEFT JOIN wa.whatsmeow_chat_settings cs ON ch.jid = cs.chat_jid
        WHERE (cs.archived IS NULL OR cs.archived = 0)
          AND ch.jid != 'status@broadcast'
        ORDER BY ch.last_message_time DESC
    """).fetchall()

    conn.close()
    return [(r["jid"], r["name"] or r["jid"].split("@")[0]) for r in rows]
```

---

## 10. Summary: One Rule

> **Always write `WHERE (cs.archived IS NULL OR cs.archived = 0)`.**
> Never write `WHERE cs.archived = 0` alone — it silently drops chats that were never
> explicitly unarchived and have no row in the settings table.