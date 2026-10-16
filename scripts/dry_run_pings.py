"""Scripted dry-run: evaluate ping rules over a snapshot sample.

Reads ONLY /tmp/mosaic-test/messages.db read-only. Prints decisions,
never sends, never writes to the snapshot, never touches the network
(no LLM client is constructed). State goes to a temp MOSAIC_DATA_DIR.

Usage: source .venv/bin/activate && python scripts/dry_run_pings.py
"""

import datetime
import os
import sqlite3
import sys
import tempfile

SNAPSHOT = "/tmp/mosaic-test/messages.db"

_tmp = tempfile.mkdtemp(prefix="mosaic-pingdryrun-")
os.environ.setdefault("MOSAIC_DATA_DIR", _tmp)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hermes_bot import pings as pg  # noqa: E402
from hermes_bot import scanner as sc  # noqa: E402
from hermes_bot import store as st  # noqa: E402


def _owner_phone() -> str:
    try:
        from hermes_bot import identity as _ident
        phone, _, _ = _ident.own_identity(
            session_db="/tmp/mosaic-test/whatsapp.db")
        return phone or ""
    except Exception:
        return ""


def main() -> None:
    conn = st.connect()
    st.init_db(conn)
    snap = sqlite3.connect(f"file:{SNAPSHOT}?mode=ro", uri=True)
    snap.row_factory = sqlite3.Row

    now = datetime.datetime.now().astimezone()
    owner_phone = _owner_phone()
    tokens = tuple(t for t in (owner_phone,) if t)

    excluded = sc.archived_jids(snap)
    chats = [
        r for r in snap.execute(
            "SELECT jid FROM chats WHERE jid LIKE '%@g.us' "
            "ORDER BY last_message_time DESC").fetchall()
        if r[0] not in excluded][:20]

    fired, suppressed = 0, {}
    for (jid,) in chats:
        last = snap.execute(
            "SELECT content, timestamp, is_from_me FROM messages "
            "WHERE chat_jid = ? AND content != '' "
            "ORDER BY timestamp DESC LIMIT 1", (jid,)).fetchone()
        if not last:
            continue
        own = snap.execute(
            "SELECT MAX(timestamp) m FROM messages WHERE chat_jid = ? "
            "AND is_from_me = 1", (jid,)).fetchone()
        try:
            last_t = datetime.datetime.fromisoformat(
                last["timestamp"].replace(" ", "T"))
            if last_t.tzinfo is None:
                last_t = last_t.replace(tzinfo=datetime.timezone.utc)
            if own and own["m"]:
                own_t = datetime.datetime.fromisoformat(
                    own["m"].replace(" ", "T"))
                if own_t.tzinfo is None:
                    own_t = own_t.replace(tzinfo=datetime.timezone.utc)
                silence = (now - own_t).total_seconds()
            else:
                silence = float("inf")
        except ValueError:
            continue
        direct = pg.is_direct_address(last["content"] or "", jid, tokens)
        fire, reason = pg.should_ping(
            direct=direct, owner_silence_s=silence,
            muted=st.is_muted(conn, jid), pings_today=0, now=now)
        if fire:
            fired += 1
            print(f"PING  {jid[:24]}  silence={silence / 60:.0f}m  "
                  f"{(last['content'] or '')[:70]}")
        else:
            suppressed[reason] = suppressed.get(reason, 0) + 1
    print(f"chats sampled : {len(chats)}")
    print(f"would ping    : {fired}")
    for reason, n in sorted(suppressed.items()):
        print(f"suppressed-{reason} : {n}")
    snap.close()
    conn.close()


if __name__ == "__main__":
    main()
