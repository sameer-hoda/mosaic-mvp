"""Read-only pipeline watcher (fix 9): cycle, inbox, keep/junk, outbox.

Stdlib only. Never writes, never sends. Usage:
    MOSAIC_DATA_DIR=/path/to/store python scripts/watch_mosaic.py
"""

import os
import sqlite3
import sys
import time


def _ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _chat_name(snap, jid):
    try:
        row = snap.execute("SELECT name FROM chats WHERE jid = ?",
                           (jid,)).fetchone()
        return (row["name"] or jid) if row else jid
    except Exception:
        return jid


def collect(mosaic_db, snapshot_db):
    """Gather dashboard state. Every read is guarded; gaps show as n/a."""
    data = {"items": [], "junk": {}, "cycle": None, "outbox": "n/a",
            "chats": "n/a", "contacts": "n/a"}
    try:
        db = _ro(mosaic_db)
        try:
            data["items"] = [dict(r) for r in db.execute(
                "SELECT what, chat_jid, kind, status FROM items"
                " WHERE status = 'open' ORDER BY updated_at DESC LIMIT 10")]
            data["junk"] = {r[0]: r[1] for r in db.execute(
                "SELECT substr(detail, 1, instr(detail, ':') - 1), count(*)"
                " FROM event_log WHERE kind = 'junk' GROUP BY 1")}
            row = db.execute(
                "SELECT ts, detail FROM event_log WHERE kind = 'cycle'"
                " ORDER BY ts DESC LIMIT 1").fetchone()
            if row:
                data["cycle"] = {"ago_s": round(time.time() - row["ts"], 1),
                                 "detail": row["detail"]}
        finally:
            db.close()
    except Exception as exc:
        data["error"] = f"mosaic.db unreadable: {exc}"
        return data
    try:
        snap = _ro(snapshot_db)
        try:
            for item in data["items"]:
                item["chat_name"] = _chat_name(snap, item["chat_jid"])
            try:
                data["chats"] = snap.execute(
                    "SELECT count(*) FROM chats").fetchone()[0]
            except Exception:
                pass
            try:
                data["contacts"] = snap.execute(
                    "SELECT count(*) FROM whatsmeow_contacts").fetchone()[0]
            except Exception:
                pass
        finally:
            snap.close()
    except Exception:
        pass
    return data


def collect_outbox(hermes_db):
    try:
        db = _ro(hermes_db)
        try:
            return db.execute(
                "SELECT count(*) FROM outbox WHERE sent_at IS NULL"
            ).fetchone()[0]
        finally:
            db.close()
    except Exception:
        return "n/a"


def render(data):
    lines = ["== mosaic watch =="]
    cyc = data.get("cycle")
    lines.append("cycle: " + (f"{cyc['ago_s']}s ago ({cyc['detail']})"
                              if cyc else "no heartbeat yet"))
    lines.append(f"inbox: {len(data.get('items', []))} open shown"
                 + "".join(f"\n  - [{i.get('kind')}] {i.get('what', '')[:60]}"
                           f" ({i.get('chat_name', i.get('chat_jid', ''))[:30]})"
                           for i in data.get("items", [])[:5]))
    junk = data.get("junk", {})
    lines.append("junk: " + (", ".join(f"{k}={v}" for k, v in junk.items())
                             or "none logged"))
    lines.append(f"outbox unsent: {data.get('outbox')}")
    lines.append(f"chats: {data.get('chats')}  contacts: {data.get('contacts')}")
    if data.get("error"):
        lines.append("note: " + data["error"])
    return "\n".join(lines)


def main() -> int:
    data_dir = os.getenv("MOSAIC_DATA_DIR", "hermes_bot/store")
    mosaic_db = os.path.join(data_dir, "mosaic.db")
    snapshot_db = (os.getenv("MOSAIC_SNAPSHOT_PATH")
                   or "/tmp/mosaic-test/messages.db")
    hermes_db = os.path.join(data_dir, "hermes.db")
    data = collect(mosaic_db, snapshot_db)
    data["outbox"] = collect_outbox(hermes_db)
    print(render(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
