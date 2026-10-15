"""Dry-run: render one morning brief from a 20-chat snapshot sample.

Prints only. No sends, no network, no writes to the snapshot.
Usage: source .venv/bin/activate && python scripts/morning_brief_dryrun.py [--owner Sam]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermes_bot import briefs, extract as ex
from hermes_bot import scanner, store as st


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", default=os.getenv("OWNER_NAMES", ""),
                    help="comma-separated owner first names")
    args = ap.parse_args()
    owner_names = [n.strip() for n in args.owner.split(",") if n.strip()]

    tmp = tempfile.mkdtemp(prefix="mosaic-dryrun-")
    os.environ["MOSAIC_DATA_DIR"] = tmp
    store_conn = st.connect()
    st.init_db(store_conn)

    snap = scanner.open_snapshot()
    try:
        chats = scanner.list_recent_chats(snap, 20)
        print(f"sampled {len(chats)} chats (read-only)")
        n_msgs = 0
        for chat in chats:
            rows = scanner.fetch_new_messages(snap, chat["jid"], "", limit=500)
            if not rows:
                continue
            n_msgs += len(rows)
            thread = ex.enrich_thread(
                scanner._to_thread(chat["jid"], chat.get("name", ""), rows),
                owner_names)
            got = ex.heuristic_extract(thread) or {}
            for raw in got.get("items", [])[:4]:
                raw = dict(raw)
                raw["chat_jid"] = chat["jid"]
                st.upsert_item(store_conn, raw)
        print(f"scanned {n_msgs} messages, {len(briefs.fetch_open_items(store_conn))} open items")
        ranked = briefs.owner_next_items(briefs.fetch_open_items(store_conn), owner_names)
        text, shown, _ = briefs.render_brief(ranked, "Morning brief",
                                             also_moved=ranked[len(ranked[:5]):][:2])
        print("--- morning brief (dry-run, not sent) ---")
        print(text)
    finally:
        snap.close()
        store_conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
