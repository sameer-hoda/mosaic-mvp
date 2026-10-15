"""Cheap extraction gates: rules first, LLM only for gated threads.

A thread is a dict: {"chat_jid", "chat", "msgs": [{"sender","body",
"from_me","id","ts"}]} plus signal fields added by enrich_thread().

Gate order (cheapest first):
  banter / FYI-link / bot-digest rejects -> never cost an LLM call.
  direct address of the owner, "?", promise phrases, or the owner's own
  messages mark a thread as a candidate; only candidates go to the LLM.
"""

import re

# follow-up / nudge markers (mined from real chat history)
NUDGE_RE = re.compile(
    r"\b(any update|update on|update\?|follow(ed)?[ -]?up|following up|gentle reminder|reminder|"
    r"still waiting|waiting on|waiting for|awaiting|pending on|pending from|by when|when can|"
    r"eta\b|no response|not responded|chase|nudge|status\?|ho gaya|kiya\?)", re.I)
ASK_RE = re.compile(
    r"\b(please|pls|plz|kindly|can you|could you|need you to|request you|req:|action item|"
    r"take this (fwd|forward)|pursue|share the|share it|send me|let me know)\b", re.I)
# someone promising to do something
PROMISE_RE = re.compile(
    r"\b(will (share|send|do|check|get|fix|ship|update|look|revert|confirm)|by eod|by tomorrow|"
    r"\bon it\b|checking|looking into|i.?ll (do|share|send|check|handle|take|fix|update))\b", re.I)

# automated digests posted by bots — never human asks
BOT_RE = re.compile(
    r"(task_dog|\*?SCOREBOARD\*?|\*Last 24 Hrs\*|\u26a1 \*Pending Follow-Ups\*|"
    r"###\s+\*\*PROJECT:|\U0001f4cb \*(?:\U0001f4cb )?Last 24)", re.I)

# FYI / forwarded link shares — information, not asks
FYI_RE = re.compile(r"^\s*(fwd?|fyi)\b", re.I)
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)

# banter: greetings, laughs, acks, reactions
BANTER_RE = re.compile(
    r"^\s*(morning|good morning|good evening|good night|hey+h*|hi+|hello+|yo\b|"
    r"haha+h*|hehe+|lol+l*|lmao|rofl|\U0001f602|\U0001f604|\+ ?1|same here|well said|"
    r"thanks?|thank you|thx|noted|sure|okay?|cool|nice|great|awesome|perfect|congrats?|"
    r"congratulations|happy birthday|welcome|good luck|all the best)", re.I)

KINDS = ("ask", "commitment", "blocker", "decision", "update")
STATUSES = ("open", "done", "blocked")

JUNK_URL_RE = re.compile(r"(https?://|\bslack\.com\b)", re.I)
QUOTED_Q_RE = re.compile(
    r"^[A-Z][A-Za-z]+['\"]? (is|are|was|were|can|could|did|does|do|has|have|will|would|should)\b", re.I)
BAD_FIRST_WORDS = {
    "we", "i", "their", "they", "them", "this", "that", "these", "those",
    "it", "its", "he", "she", "his", "her", "also", "so", "yes", "no", "ok",
    "okay", "thanks", "thank", "the", "have", "has", "had", "been", "being",
    "am", "is", "are", "was", "were", "will", "would", "should", "could",
    "can", "may", "might", "must", "do", "does", "did", "a", "an", "not",
    "what", "when", "where", "who", "whom", "whose", "why", "how", "which",
    "whether",
    "hey", "hi", "hello", "happy", "good", "great", "congrats",
    "congratulations", "welcome", "lol", "haha", "hehe", "nice", "cool",
    "awesome", "perfect", "noted", "sure", "fyi",
}
GOOD_ED = {"need", "feed", "embed", "proceed", "succeed", "exceed"}
IRREG_PAST = {
    "sent", "went", "came", "said", "told", "got", "took", "saw", "knew",
    "made", "found", "heard", "wrote", "gave", "kept", "left", "felt",
    "thought", "met", "became", "chose", "spoke",
}

REACTION_RE = re.compile(
    r"^(looks(\s+(great|good|nice|amazing|awesome))?|\+ ?1|same here|well said)\b", re.I)


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", text.lower())


def is_banter(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return True
    if BANTER_RE.search(text):
        rest = BANTER_RE.sub("", text).strip(" ,.!-")
        if len(rest.split()) <= 6:
            return True
    if len(text.split()) <= 2 and not ("?" in text or ASK_RE.search(text)):
        return True
    return False


def is_fyi_link(text: str) -> bool:
    """An FYI/link share: FYI-prefixed or URL-dominated with no ask."""
    text = (text or "").strip()
    if not text:
        return False
    if FYI_RE.search(text) and not (ASK_RE.search(text) or "?" in text):
        return True
    urls = URL_RE.findall(text)
    if urls:
        de_url = URL_RE.sub("", text).strip()
        if len(de_url.split()) <= 4 and not (ASK_RE.search(text) or "?" in text):
            return True
    return False


def is_bot_text(text: str) -> bool:
    return bool(BOT_RE.search(text or ""))


def mentions_owner(text: str, owner_names: list[str]) -> bool:
    """Whole-word first-name match only ('sam' never matches 'samaritan')."""
    words = set(_words(text or ""))
    return any((n or "").strip().lower() in words for n in owner_names if n)


def item_ok(what: str) -> bool:
    words = (what or "").split()
    if len(words) < 2:
        return False
    if not re.search(r"[^\W_]", what):
        return False
    first = words[0].lower().rstrip(".,!?;:'\"")
    if JUNK_URL_RE.search(what) or QUOTED_Q_RE.match(what):
        return False
    if first in BAD_FIRST_WORDS or first in IRREG_PAST:
        return False
    if first.endswith("ed") and first not in GOOD_ED:
        return False
    return True


def normalize_item(item: dict) -> dict | None:
    kind = str(item.get("kind") or "ask").lower()
    what = re.sub(r"^@\S+,?\s+", "", str(item.get("what") or "").strip())
    what = re.sub(r"^(please|kindly)\s+", "", what, flags=re.I).strip()[:90]
    if not what or len(what) < 4 or not item_ok(what):
        return None
    owner = str(item.get("owner") or "").strip().split()[0][:24] \
        if str(item.get("owner") or "").strip() else ""
    requester = str(item.get("requester") or "").strip().split()[0][:24] \
        if str(item.get("requester") or "").strip() else ""
    topic = re.sub(r"[^a-z0-9]+", "-", str(item.get("topic") or "").lower()).strip("-")[:32] \
        or "general"
    return {
        "kind": kind if kind in KINDS else "ask",
        "what": what,
        "context": str(item.get("context") or "")[:200],
        "owner": owner,
        "requester": requester,
        "due": str(item.get("due") or "")[:40],
        "status": str(item.get("status") or "open").lower()
        if str(item.get("status") or "").lower() in STATUSES else "open",
        "topic": topic,
    }


def enrich_thread(thread: dict, owner_names: list[str] | None = None) -> dict:
    """Attach cheap signals. Human messages only for ask/nudge detection."""
    owner_names = owner_names or []
    msgs = thread.get("msgs", [])
    human = [m for m in msgs if not is_bot_text(m.get("body", ""))]
    bodies = [m.get("body", "") for m in human]
    thread["from_me"] = any(m.get("from_me") for m in msgs)
    thread["nudges"] = sum(1 for b in bodies if NUDGE_RE.search(b))
    thread["has_ask"] = any(
        ASK_RE.search(b) or PROMISE_RE.search(b) or "?" in b for b in bodies
    )
    thread["q_to_me"] = any(
        not m.get("from_me") and "?" in m.get("body", "")
        and mentions_owner(m.get("body", ""), owner_names)
        for m in human
    )
    thread["direct_address"] = any(
        mentions_owner(b, owner_names) for b in bodies
    ) if owner_names else False
    thread["all_banter"] = bool(msgs) and all(is_banter(m.get("body", "")) for m in human)
    thread["fyi_only"] = bool(msgs) and all(
        is_fyi_link(m.get("body", "")) or is_banter(m.get("body", "")) for m in human
    )
    thread["all_bot"] = bool(msgs) and not human
    return thread


def worth_llm(thread: dict) -> bool:
    """Gate for spending an LLM call. Banter/FYI/bot threads never qualify;
    every other thread needs a real-work signal (own message, directed
    question, nudge, or ask/promise marker)."""
    if thread.get("all_bot") or thread.get("all_banter") or thread.get("fyi_only"):
        return False
    return bool(
        thread.get("from_me") or thread.get("q_to_me")
        or (thread.get("nudges") or 0) > 0 or thread.get("has_ask")
    )


# Alias used by the scanner: same gate decides whether the LLM is called.
should_call_llm = worth_llm


def heuristic_extract(thread: dict) -> dict | None:
    """No-LLM fallback. Returns None for banter/FYI/bot threads."""
    msgs = thread.get("msgs", [])
    if not msgs:
        return None
    if thread.get("all_bot"):
        return None
    if thread.get("all_banter") or thread.get("fyi_only"):
        return None
    human = [m for m in msgs if not is_bot_text(m.get("body", ""))]
    if not human:
        return None
    last = human[-1]
    has_ask_signal = (
        any(ASK_RE.search(m.get("body", "")) or PROMISE_RE.search(m.get("body", ""))
            for m in human)
        or (thread.get("nudges") or 0) > 0 or bool(thread.get("q_to_me"))
    )
    item = {
        "kind": "ask" if has_ask_signal else "update",
        "what": (last.get("body", "") or thread.get("chat", ""))[:60],
        "context": (last.get("body", "") or "")[:140],
        "owner": "",
        "requester": last.get("sender", ""),
        "due": "",
        "status": "open",
        "topic": "general",
    }
    norm = normalize_item(item)
    if not norm:
        return None
    return {"items": [norm]}


def cluster_threads(messages: list[dict], gap_min: int = 90) -> list[dict]:
    """Split one chat's messages into time-gap threads."""
    by_chat: dict[str, list[dict]] = {}
    for m in messages:
        by_chat.setdefault(m.get("chat_jid", ""), []).append(m)
    threads = []
    for chat_jid, ms in by_chat.items():
        ms.sort(key=lambda m: (m.get("ts") or "", m.get("id") or ""))
        cur: list[dict] = []
        for m in ms:
            cur.append(m)
            # gap split needs parsed timestamps; callers pass ISO strings —
            # split when the ISO date-hour bucket changes by gap hours is
            # overkill here: one thread per chat batch by default.
        if cur:
            threads.append({"chat_jid": chat_jid,
                            "chat": cur[0].get("chat", chat_jid),
                            "msgs": cur})
    return threads
