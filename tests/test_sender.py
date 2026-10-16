"""Fix 7: per-boot token provisioning; drops log destination, never text."""

import logging
import sqlite3

from hermes_bot import sender


def _outbox(tmp_path, monkeypatch):
    import hermes_bot.config as cfg
    db = tmp_path / "hermes.db"
    monkeypatch.setattr(cfg, "HERMES_DB_PATH", str(db))
    conn = sender._get_outbox_db()
    return conn


def test_group_refused_by_default(tmp_path, monkeypatch):
    import pytest
    _outbox(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        sender.enqueue_message("family@g.us", "hi")


def test_group_accepted_via_confirmed_yes(tmp_path, monkeypatch):
    _outbox(tmp_path, monkeypatch)
    sender.enqueue_message("family@g.us", "hi", via_confirmed_yes=True)


def test_final_drop_logs_jid_not_text(tmp_path, monkeypatch, caplog):
    conn = _outbox(tmp_path, monkeypatch)
    conn.execute("INSERT INTO outbox (jid, text, queued_at, attempts)"
                 " VALUES (?,?,?,?)",
                 ("919999999999@s.whatsapp.net", "secret-words-here", 1.0, 2))
    conn.commit()
    with caplog.at_level(logging.WARNING, logger="mosaic.sender"):
        sender._handle_failure(conn, 1, "919999999999@s.whatsapp.net", 2)
    assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
    assert "919999999999@s.whatsapp.net" in caplog.text
    assert "secret-words-here" not in caplog.text
    conn.close()
