"""Worker D gemini client: strict JSON schema, token logging, fake keys only."""

import json
import os

import pytest

from hermes_bot import gemini as gm
from hermes_bot import store as st


class _FakeUsage:
    prompt_token_count = 120
    candidates_token_count = 30


class _FakeResp:
    text = '{"items": [{"kind": "ask", "what": "Share the numbers by EOD", "status": "open"}]}'
    usage_metadata = _FakeUsage()


class _FakeModels:
    def __init__(self, outer):
        self.outer = outer

    def generate_content(self, model, contents, config=None):
        self.outer.seen.append({"model": model, "contents": contents, "config": config})
        if self.outer.fail:
            raise RuntimeError("boom")
        return _FakeResp()


class _FakeClient:
    def __init__(self, fail=False):
        self.seen = []
        self.fail = fail
        self.models = _FakeModels(self)


@pytest.fixture()
def store_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    c = st.connect()
    st.init_db(c)
    yield c
    c.close()


def test_uses_flash_lite_model_by_default(store_conn):
    client = _FakeClient()
    gm.extract_items("hello", client=client, store_conn=store_conn)
    assert client.seen[0]["model"] == "gemini-3.5-flash-lite"


def test_strict_json_schema_requested(store_conn):
    client = _FakeClient()
    gm.extract_items("hello", client=client, store_conn=store_conn)
    cfg = client.seen[0]["config"]
    assert cfg["response_mime_type"] == "application/json"
    assert cfg["response_json_schema"]["required"] == ["items"]


def test_every_call_logs_model_and_tokens(store_conn):
    gm.extract_items("hello", client=_FakeClient(), store_conn=store_conn)
    row = store_conn.execute("SELECT * FROM event_log").fetchone()
    assert row["kind"] == "gemini_call"
    assert row["model"] == "gemini-3.5-flash-lite"
    assert row["input_tokens"] == 120
    assert row["output_tokens"] == 30


def test_items_parsed_and_capped(store_conn):
    got = gm.extract_items("hello", client=_FakeClient(), store_conn=store_conn)
    assert got["items"][0]["what"] == "Share the numbers by EOD"


def test_failure_is_fail_open_and_logged(store_conn):
    got = gm.extract_items("hello", client=_FakeClient(fail=True), store_conn=store_conn)
    assert got == {"items": []}
    rows = store_conn.execute("SELECT * FROM event_log").fetchall()
    assert len(rows) == 1
    assert "error" in rows[0]["detail"]


def test_key_never_in_logs_or_output(store_conn, capsys):
    client = _FakeClient()
    gm.extract_items("hello", client=client, store_conn=store_conn,
                     api_key="fake-test-key")
    out = capsys.readouterr().out
    assert "fake-test-key" not in out
    for row in store_conn.execute("SELECT * FROM event_log").fetchall():
        assert "fake-test-key" not in (row["detail"] + row["model"])
        assert "fake-test-key" not in json.dumps(dict(row))
    assert "fake-test-key" not in json.dumps(client.seen[0]["contents"])


def test_missing_key_is_fail_open(store_conn, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    got = gm.extract_items("hello", store_conn=store_conn)
    assert got == {"items": []}


def test_env_key_wins_over_saved_file(monkeypatch, tmp_path):
    from hermes_bot import config as cfg
    import json
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"gemini_api_key": "saved-key"}))
    monkeypatch.setattr(cfg, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    import hermes_bot.gemini as gm
    assert gm.get_api_key() == "env-key"


def test_saved_key_used_when_env_missing_and_reread_live(monkeypatch, tmp_path):
    from hermes_bot import config as cfg
    import json
    import hermes_bot.gemini as gm
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"gemini_api_key": "saved-key"}))
    monkeypatch.setattr(cfg, "CONFIG_FILE", str(cfg_file))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert gm.get_api_key() == "saved-key"
    cfg_file.write_text(json.dumps({"gemini_api_key": "rotated-key"}))
    assert gm.get_api_key() == "rotated-key"
