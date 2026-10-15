# docs — Index

Documentation collated from `all_docs/` plus new master guides.

## Core / New Guides

| Doc | What it covers |
|-----|----------------|
| **`contact_resolution.md`** | **The** guide for resolving WhatsApp JID / LID / phone → human names across every component. Includes the canonical SQL, special cases (MeChat, group senders), and where each implementation lives. |
| **`archived_chat_detection.md`** | **How archived/non-archived chat filtering works.** The `whatsmeow_chat_settings` table, the `NULL OR 0` pattern, the two query patterns used everywhere, and the distinction from wa-pull's task archive. |
| `whatsapp_database_master_guide.md` | Full schema of `whatsapp.db` + `messages.db`, relationships. |
| `startup_manual.md` | TaskDog setup & run: bridge → backend → frontend. |
| `bridge_README.md` | The Go WhatsApp bridge: what it does + REST API endpoints. |

## Backend / Frontend

- `backend_API_DOCUMENTATION.md` — TaskDog backend REST API (port 3001).
- `backend_README.md` — TaskDog backend architecture.
- `frontend_README.md` — Lovable frontend project notes.
- `PROJECT_CLARIFICATION.md` — which codebase is "the final version".

## Operations / Infrastructure

- `EC2_ACCESS_GUIDE.md` — SSH/deploy details for `task_dog_wa` (15.134.203.162).
- `sync_prod_data.md` — pull latest DBs from EC2.
- `troubleshooting_guide.md` — daily-report cron debugging.
- `handover.md` — handover notes (data sync + frontend fixes).
- `DAILY_REPORT_V1_ANALYSIS.md` — post-mortem of the 9 AM daily report (V1).

## Product Logic

- `MANAGING_INITIATIVES.md` — how to add/remove initiatives in reports.
- `wiki_approach.md` — "Wiki of Everything" system spec (5-layer model).

## archive_docs/ (legacy but useful)

- `contact_resolution_guide.md` — original contact-resolution narrative (superseded by `contact_resolution.md`).
- `whatsapp_database_schema.md` — detailed table-by-table schema.
- `PROJECT_CONTEXT.md` — original project context.
- `project_master_manual.md` — older master manual.
- `README_TECHNICAL_GLOSSARY.md` — glossary of terms.
- `pending_threads_bot_design.md` — pending-threads bot design doc.
