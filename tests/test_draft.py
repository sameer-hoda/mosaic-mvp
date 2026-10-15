"""Worker F: draft N returns 3 grounded reply options (A/B/C).

Strict schema + grounding check: a draft must never invent dates or
numbers that do not appear in the thread.
"""


def _draft():
    from hermes_bot import draft as d
    return d


def test_build_drafts_returns_three_labeled_options():
    d = _draft()
    item = {"chat_jid": "c@s.whatsapp.net", "what": "Share the Q3 numbers"}
    thread = ["Dev: Sameer, please share the Q3 numbers by Friday?",
              "Sameer: will pull them tomorrow"]
    got = d.build_drafts(item, thread)
    assert [o["label"] for o in got["options"]] == ["A", "B", "C"]
    for opt in got["options"]:
        assert opt["text"].strip(), "option text must not be empty"
    assert len({o["text"] for o in got["options"]}) == 3


def test_draft_schema_has_grounded_field():
    d = _draft()
    assert "grounded" in d.DRAFT_SCHEMA["option"]["required"]
    assert d.DRAFT_SCHEMA["option"]["properties"]["grounded"]["type"] == "boolean"


def test_heuristic_options_are_grounded():
    d = _draft()
    item = {"chat_jid": "c@s.whatsapp.net", "what": "Share the Q3 numbers"}
    thread = ["Dev: Sameer, please share the Q3 numbers by Friday?",
              "Sameer: will pull them tomorrow"]
    got = d.build_drafts(item, thread)
    for opt in got["options"]:
        assert opt["grounded"] is True


def test_llm_output_with_invented_date_fails_grounding():
    d = _draft()
    item = {"chat_jid": "c@s.whatsapp.net", "what": "Confirm the visit"}
    thread = ["Asha: can you visit next week?"]
    got = d.build_drafts(
        item, thread,
        llm=lambda prompt: ["Sure, visiting on 25 December.",
                            "Yes, see you then.", "OK, noted."])
    invented = [t for o in got["options"] for t in
                d.check_grounded(" ".join(thread), o["text"])["invented"]]
    assert any("25" in t for t in invented)
    assert got["options"][0]["grounded"] is False


def test_check_grounded_allows_thread_numbers():
    d = _draft()
    thread = "Dev: share the 3 files by 5pm, budget is 5000"
    res = d.check_grounded(thread, "Sharing the 3 files by 5pm, budget 5000. OK?")
    assert res == {"grounded": True, "invented": []}


def test_refs_round_trip_case_insensitive(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    from hermes_bot import store as st
    d = _draft()
    conn = st.connect()
    st.init_db(conn)
    opts = [{"label": "A", "text": "a", "grounded": True},
            {"label": "B", "text": "b", "grounded": True},
            {"label": "C", "text": "c", "grounded": True}]
    assert d.remember_options(conn, "2", "c@s.whatsapp.net", "Dev", opts) == [
        "2A", "2B", "2C"]
    assert d.lookup_option(conn, "2b")["text"] == "b"
    assert d.lookup_option(conn, "9Z") is None
    conn.close()


def test_lookup_item_uses_freshest_unexpired_alias(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    from hermes_bot import store as st
    d = _draft()
    conn = st.connect()
    st.init_db(conn)
    item = st.upsert_item(conn, {"chat_jid": "c@s.whatsapp.net",
                                 "what": "Call back"})
    st.set_alias_map(conn, 3, {"2": item["id"]}, now=time.time())
    brief_no, found = d.lookup_item(conn, "2")
    assert (brief_no, found["id"]) == (3, item["id"])
    assert d.lookup_item(conn, "8") is None
    conn.close()


def test_format_reply_lists_options_with_ref():
    d = _draft()
    text = d.format_draft_reply("2", "Dev", "Share the Q3 numbers",
                                [{"label": "A", "text": "a", "grounded": True},
                                 {"label": "B", "text": "b", "grounded": True},
                                 {"label": "C", "text": "c", "grounded": True}])
    assert "Dev" in text and "send 2A" in text and "(A)" in text
