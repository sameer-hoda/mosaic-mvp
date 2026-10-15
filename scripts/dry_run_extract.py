"""Scripted dry-run extraction over a 20-chat sample of the snapshot.

Reads ONLY /tmp/mosaic-test/messages.db read-only. Never sends, never
writes to the snapshot, never touches the network (heuristic path only —
no LLM client is constructed). State goes to a temp MOSAIC_DATA_DIR.

Usage: source .venv/bin/activate && python scripts/dry_run_extract.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_tmp = tempfile.mkdtemp(prefix="mosaic-dryrun-")
os.environ.setdefault("MOSAIC_DATA_DIR", _tmp)

from hermes_bot import scanner as sc  # noqa: E402
from hermes_bot import store as st  # noqa: E402


def main() -> None:
    conn = st.connect()
    st.init_db(conn)
    summary = sc.scan(conn, limit_chats=20, dry_run=True)
    print(f"chats scanned : {summary['chats']}")
    print(f"messages seen : {summary['messages']}")
    print(f"items found   : {summary['items']}")
    print(f"llm calls     : {summary['llm_calls']}")
    for name, count in sorted(summary["per_chat"].items(),
                              key=lambda kv: kv[1], reverse=True)[:20]:
        print(f"  {count:3d}  {name}")
    conn.close()


if __name__ == "__main__":
    main()
