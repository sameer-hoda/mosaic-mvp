#!/usr/bin/env python3
"""End-to-end dry-run: scan → extract → brief → ping-eval → draft.

Reads ONLY /tmp/mosaic-test/messages.db read-only. Never sends, never
writes to the snapshot, and refuses to run when MOSAIC_LIVE=1. At most
30 live Gemini calls total (gated threads beyond budget fall back to
cheap rules); the API key is read from env and never printed.

Usage:
    source .venv/bin/activate
    GEMINI_API_KEY=... OWNER_NAMES=Sam python scripts/e2e_dry_run.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

SAMPLE_CHATS = 100
LIVE_CALL_BUDGET = 30

# Clearly-labelled assumption for the cost line (Flash-Lite class rates).
ASSUMED_IN_PER_1M = 0.10
ASSUMED_OUT_PER_1M = 0.40

if os.getenv("MOSAIC_LIVE", "") == "1":
    print("e2e_dry_run: refusing to run with MOSAIC_LIVE=1 (dry-run only).",
          file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix="mosaic-e2e-")
os.environ["MOSAIC_DATA_DIR"] = _tmp

from hermes_bot import briefs  # noqa: E402
from hermes_bot import cycle  # noqa: E402
from hermes_bot import extract as ex  # noqa: E402
from hermes_bot import gemini as gm  # noqa: E402
from hermes_bot import scanner as sc  # noqa: E402
from hermes_bot import store as st  # noqa: E402

HAVE_KEY = bool(os.getenv("GEMINI_API_KEY", ""))
OWNER_NAMES = [n.strip() for n in os.getenv("OWNER_NAMES", "").split(",")
               if n.strip()]


def owner_phones(snap, limit: int = 3) -> list:
    """Most common digit-like senders of own messages (owner's phone forms).

    Derived from messages.db only — no session store, no network.
    """
    try:
        rows = snap.execute(
            "SELECT sender, COUNT(*) c FROM messages WHERE is_from_me = 1 "
            "AND sender NOT LIKE '%@g.us' GROUP BY sender "
            "ORDER BY c DESC LIMIT ?",
            (limit,)).fetchall()
    except Exception:
        return []
    phones = []
    for r in rows:
        user = str(r["sender"] or "").split("@")[0].split(":")[0]
        if user.isdigit() and len(user) >= 8:
            phones.append(user)
    return phones


def main() -> int:
    store_conn = st.connect()
    st.init_db(store_conn)
    live_calls = {"n": 0}

    def capped_llm(thread: dict) -> dict:
        if live_calls["n"] < LIVE_CALL_BUDGET and HAVE_KEY:
            live_calls["n"] += 1
            lines = [f"{m.get('sender', '?')}: {m.get('body', '')}"
                     for m in thread.get("msgs", [])]
            body = "\n".join(lines)
            got = gm.extract_items(body, chat=thread.get("chat", ""),
                                   n=len(lines), store_conn=store_conn)
            return {"items": got.get("items", [])}
        return ex.heuristic_extract(thread) or {"items": []}

    summary = sc.scan(store_conn, limit_chats=SAMPLE_CHATS,
                      llm_fn=capped_llm, dry_run=True)

    snap = sc.open_snapshot()
    try:
        items = briefs.fetch_open_items(store_conn)
        with open(os.path.join(_tmp, "items.json"), "w") as fh:
            json.dump(items, fh, default=str)
        text, brief_no = briefs.build_brief(store_conn, OWNER_NAMES,
                                            slot="Morning brief")
        tokens = tuple(OWNER_NAMES + owner_phones(snap))
        ping_stat = cycle.eval_pings(store_conn, snap, tokens,
                                     limit=SAMPLE_CHATS)
        sample = cycle.draft_sample(store_conn, snap, OWNER_NAMES)
    finally:
        snap.close()

    rows = store_conn.execute(
        "SELECT COALESCE(SUM(input_tokens),0) i, "
        "COALESCE(SUM(output_tokens),0) o FROM event_log "
        "WHERE kind='gemini_call'").fetchone()
    in_tok, out_tok = int(rows["i"]), int(rows["o"])
    cost = (in_tok / 1e6) * ASSUMED_IN_PER_1M + \
        (out_tok / 1e6) * ASSUMED_OUT_PER_1M

    print(f"chats scanned : {summary['chats']}")
    print(f"messages seen : {summary['messages']}")
    print(f"items found   : {summary['items']} "
          f"(open in db: {len(items)})")
    print(f"live gemini   : {live_calls['n']}/{LIVE_CALL_BUDGET} "
          f"({'key present' if HAVE_KEY else 'no key — rules only'})")
    print(f"heuristic/stub: {summary['llm_calls'] - live_calls['n']} "
          f"gated threads beyond budget ran on rules")
    print(f"tokens in/out : {in_tok} / {out_tok}")
    print(f"est. cost     : ${cost:.4f} (assumed "
          f"${ASSUMED_IN_PER_1M}/1M in + ${ASSUMED_OUT_PER_1M}/1M out)")
    print(f"pings would-fire : {ping_stat['fired']}")
    for reason, n in sorted(ping_stat["suppressed"].items()):
        print(f"ping suppressed-{reason} : {n}")
    print(f"--- morning brief #{brief_no} (dry-run, not sent) ---")
    print(text)
    if sample:
        print(f"--- sample draft [{sample['chat_name']}] ---")
        print(sample["text"])
    else:
        print("--- sample draft: nothing actionable, skipped ---")
    print(f"throwaway db  : {_tmp}/mosaic.db (temp dir only)")
    store_conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
