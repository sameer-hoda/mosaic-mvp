"""Worker D gates: banter/FYI never become items and never cost an LLM call.

Ported from momentum's false-positive fixtures (test_extraction_fixtures,
test_item_gates, test_pipeline_fp): real gate code, stubbed LLM with a
call counter — banter threads must not even earn a call.
"""

from hermes_bot import extract as ex


def _thread(bodies, owner_names=None, from_me_flags=None):
    msgs = []
    for i, body in enumerate(bodies):
        msgs.append({"id": f"m{i}", "sender": "Asha", "body": body,
                     "ts": f"2026-09-10T1{i}:00:00", "from_me": False})
    if from_me_flags:
        for i, flag in enumerate(from_me_flags):
            msgs[i]["from_me"] = flag
            if flag:
                msgs[i]["sender"] = "You"
    t = {"chat_jid": "work@g.us", "chat": "Work Group", "msgs": msgs}
    return ex.enrich_thread(t, owner_names or [])


def _llm_counter(calls):
    def fake_llm(thread):
        calls.append(thread)
        return {"items": []}
    return fake_llm


# ── banter: no items, no LLM call ────────────────────────────────────────────

def test_banter_thread_no_items_no_llm():
    t = _thread(["Morning all, coffee run at eleven",
                 "Haha count me in, see you there",
                 "Great weather today, lovely morning"])
    assert not ex.worth_llm(t)
    assert ex.heuristic_extract(t) is None


def test_from_me_banter_emits_nothing():
    t = _thread(["lol nice", "haha great"], from_me_flags=[True, False])
    assert ex.heuristic_extract(t) is None


def test_greeting_single_items_rejected():
    for text in ["Thanks for the update guys", "Happy birthday Rohan",
                 "Good morning team", "Noted thanks", "Done", "ETA?"]:
        assert ex.normalize_item({"kind": "ask", "what": text}) is None, text
        assert not ex.item_ok(text), text


# ── FYI links: no items, no LLM call ─────────────────────────────────────────

def test_fyi_link_share_no_items_no_llm():
    t = _thread(["FYI team, Q3 numbers are out",
                 "https://docs.google.com/q3-report"])
    assert not ex.worth_llm(t)
    assert ex.heuristic_extract(t) is None


def test_bare_url_share_rejected():
    assert ex.normalize_item(
        {"kind": "ask", "what": "https://docs.google.com/q3-report numbers"}) is None


def test_fyi_with_ask_still_candidate():
    t = _thread(["FYI team, Q3 numbers are out — please review by EOD?"])
    assert ex.worth_llm(t)


# ── quoted / past-tense / WH questions: never items ──────────────────────────

def test_quoted_question_not_an_item():
    t = _thread(["Rohan is coming tomorrow?"])
    got = ex.heuristic_extract(t)
    assert got is None or all(i["kind"] != "ask" for i in got["items"])


def test_past_tense_narration_rejected():
    for text in ["Shared the deck yesterday", "Sent the deck yesterday",
                 "Got the numbers already"]:
        assert ex.normalize_item({"kind": "ask", "what": text}) is None, text


def test_wh_questions_rejected():
    for text in ["what changed in the deploy", "how to proceed with rollout"]:
        assert not ex.item_ok(text), text


# ── bot digests: never items, never LLM ──────────────────────────────────────

def test_bot_digest_no_items_no_llm():
    t = _thread(["\u26a1 *Pending Follow-Ups* open items: merchant offers any update?",
                 "\U0001f4cb *Last 24 Hrs* 5 follow-ups pending, please review"])
    assert not ex.worth_llm(t)
    assert ex.heuristic_extract(t) is None


# ── real signals gate the LLM ────────────────────────────────────────────────

def test_direct_address_gates_llm():
    t = _thread(["Sameer, please share the numbers?"], owner_names=["Sameer"])
    assert t["q_to_me"]
    assert ex.worth_llm(t)
    assert ex.should_call_llm(t)


def test_nudge_gates_llm():
    t = _thread(["Morning all", "Any update on the rollout?"])
    assert ex.worth_llm(t)


def test_promise_phrase_gates_llm():
    t = _thread(["I will share the deck by EOD today"])
    assert ex.worth_llm(t)


def test_owner_own_message_marks_candidate():
    t = _thread(["Please share the numbers by EOD"], from_me_flags=[True])
    assert ex.worth_llm(t)


def test_mention_whole_word_only():
    assert ex.mentions_owner("Hey Sam, ETA?", ["Sam"])
    assert not ex.mentions_owner("Samaritan work is going well?", ["Sam"])
    assert not ex.mentions_owner("Hey Sam, ETA?", [])


def test_gated_thread_only_path_to_llm():
    gated = _thread(["Please share the numbers by EOD"])
    calls = []
    assert ex.should_call_llm(gated)
    _llm_counter(calls)(gated)
    assert len(calls) == 1
    banter = _thread(["Morning all, coffee run at eleven"])
    assert not ex.should_call_llm(banter)


def test_good_imperatives_kept():
    for text in ["Send the deck by EOD", "Need the numbers by EOD",
                 "Proceed with rollout", "Take this forward with Rohan"]:
        assert ex.item_ok(text), text
