"""
One-shot 5-stage query pipeline for MeChat messages.

Flow: message → Stage 1 (query prep) → route → Stages 2–5 (search pipeline)
"""

import json
import math
import random
import concurrent.futures
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from google import genai

from hermes_bot import config, db


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class QueryPlan:
    intent: str = "freeform"
    standalone_query: str = ""
    entities: list = field(default_factory=list)
    search_terms: list = field(default_factory=list)
    time_window_days: int = 14
    scope: str = "all_groups"
    group_hint: str = ""
    person_name: str = ""
    person_jid: str = ""
    cron_details: dict = field(default_factory=dict)


@dataclass
class GroupMatch:
    group_jid: str
    group_name: str
    matched_ids: list = field(default_factory=list)
    hit_count: int = 0
    score: float = 0.0


@dataclass
class RefineResult:
    group_jid: str
    group_name: str
    entity_match: bool = False
    threads: list = field(default_factory=list)
    relevant_messages: list = field(default_factory=list)


# ── Constants ─────────────────────────────────────────────────────────────────

HELP_TEXT = """🤖 *Mosaic* · Your WhatsApp Assistant

*What I can do:*
• Chat with you naturally in this chat
• Ask me "what's open" or "catch me up" — scans all groups for what needs your attention
• /ask <topic> — deep-dive search across all groups
• /cron add "<query>" daily 09:00 — schedule summaries
• /cron list — see your scheduled summaries
• /sotu — State of the Union (in any group)
• /pending — pending items (in any group)

*I understand context* — I remember what we're chatting about within a session. Just talk normally.

Need something specific? Just ask."""

GREETINGS = [
    "Hey! What can I help with?",
    "Hi there. What's on your mind?",
    "Hello! Ready to help. What do you need?",
]


# ── Stage 1 prompt ────────────────────────────────────────────────────────────

STAGE1_PROMPT = """You are the query-preparation engine for Mosaic, a personal WhatsApp assistant. Your ONLY job is to analyze the user's message and produce a structured plan. You do NOT answer the question.

Recent conversation (oldest first):
{transcript}

Known contacts (names the user's messages may refer to):
{contact_names}

New message from user: "{message}"

Respond with JSON only, no other text:

{{
  "intent": "search" | "person" | "status_check" | "cron_setup" | "cron_manage" | "freeform" | "greeting",
  "standalone_query": "",
  "entities": [
    {{
      "text": "",
      "type": "person" | "project" | "topic" | "organization",
      "aliases": []
    }}
  ],
  "search_terms": [],
  "time_window_days": 14,
  "scope": "all_groups" | "specific_group" | "person" | "none",
  "group_hint": "",
  "person_name": "",
  "cron_details": {{"query": "", "frequency": "", "time_slot": ""}}
}}

Field rules:

standalone_query — the user's message rewritten to be fully self-contained, as if sent with no prior conversation.
- Resolve pronouns and references ("he", "that project", "the second one") using the transcript.
- ONLY pull in context the new message clearly refers to. If the new message is a fresh, unrelated question, the standalone_query is just the message itself — do NOT import names, projects, or topics from the transcript that the user did not reference.
- Never add entities the user did not mention or refer to.

entities — the specific people, projects, topics, or organizations the query is about. For each, list aliases: nicknames, short forms, alternate spellings, and (for people) match against the known contacts list. If the user says "Rachit" and contacts include "Rachit Bhargava", aliases = ["Rachit", "Rachit Bhargava", "RB"]. Empty list if the query has no specific entities.

search_terms — keywords to find relevant messages by text match. Think like a search engine, not a summarizer:
- Include each entity and its aliases.
- Include topic words AND their likely variants: "invoice" → ["invoice", "payment", "bill", "paid", "transfer"].
- Include concrete nouns from the query, not filler words.
- 5–15 terms. Lowercase.

time_window_days — infer from the message: "today" = 1, "yesterday" = 2, "this week" = 7, "last month" = 30. Default 14 when unstated.

scope:
- "specific_group" only if the user names a group; put the partial name in group_hint.
- "person" if the query is about one person's messages/updates; put the best-matching contact name in person_name.
- "all_groups" for topic searches and anything needing chat context.
- "none" for freeform questions answerable without the user's chats.

intent:
- "search": wants info from their chats about a topic
- "person": wants updates from/about a specific person
- "status_check": wants open items across everything — "what's pending", "catch me up", "what did I miss" with NO named subject
- "cron_setup" / "cron_manage": scheduling or managing summaries
- "freeform": general question or statement not needing their chats
- "greeting": pure greeting or acknowledgment

Edge rules:
- A named subject beats status_check: "catch me up on the vendor situation" is "search", not "status_check".
- If the message is ambiguous between two people in contacts, still pick "person" but include BOTH names as separate entities — the downstream stages will disambiguate.
- If the transcript is empty or stale, treat the message standalone."""


# ── Stage 3 prompt ────────────────────────────────────────────────────────────

STAGE3_PROMPT = """You are a relevance-refinement engine for Mosaic.

Query: "{standalone_query}"
Target entities: {entities}

Numbered messages from "{group_name}":
{messages}

These messages were keyword-matched to the query. Your job: filter down to what's actually relevant, and disambiguate any entities.

Rules:
- *entity_match*: Is the entity mentioned here the SAME person/project the user means, or a coincidental name match? If the user asked about "Amit Mehta" and these messages mention "Amit Singh", entity_match = false.
- *threads*: Group related messages into conversational threads. Each thread gets a brief label and the message numbers belonging to it. Messages that are part of the same sub-conversation should be in the same thread.
- *relevant_ids*: List of message numbers that are genuinely relevant to the query. This is the subset you'd show the user. Do NOT include messages that match a keyword but are about a different topic.
- Empty lists are valid and common. Do not stretch. When in doubt, exclude.

Return JSON only:
{{
  "entity_match": true/false,
  "threads": [{{"label": "short thread description", "msg_ids": [1, 2, 3]}}],
  "relevant_ids": [1, 3, 5]
}}"""


# ── Stage 5 prompt ────────────────────────────────────────────────────────────

STAGE5_PROMPT = """You are Mosaic, a personal WhatsApp assistant. The user asked a question, and a search pipeline has already retrieved the relevant messages from their chats. Your job: answer the question using ONLY the messages below.

User's question: "{standalone_query}"

Search stats: searched {n_searched} groups, found relevant messages in {n_matched}: {matched_group_names}. {n_messages} messages retrieved.

Retrieved context:
{context_package}

(Format of context: one block per thread. Each block header shows GROUP, PARTICIPANTS, and DATE RANGE. Messages are verbatim with [timestamp] sender: text.)

═══ GROUNDING RULES — these override everything else ═══

1. Every factual claim MUST come from the messages above. If the messages don't contain the answer, say exactly what's missing. Never fill gaps from general knowledge or guesses about what "probably" happened.

2. Every claim MUST name its source group inline, e.g.: *Invoice approved* (Vendor Group, Aug 7)

3. NEVER merge information across groups. If "Ops Team" and "Vendor Group" both mention a deadline, report them as two separate facts from two separate groups — even if they sound related. Only connect facts across groups when the SAME named person or project explicitly appears in both.

4. A person in one group is not assumed to be the same person in another group unless the name AND context clearly match.

5. Prefer recent messages. If you cite something older than the query's time frame implies, flag its date explicitly.

6. When a single message directly settles the question, quote or closely paraphrase it with sender and date — don't dilute a definitive answer into a vague summary.

═══ RESPONSE TYPE — pick exactly one ═══

Choose the shape that best fits the question and what you found:

- "direct_answer": the question has a specific factual answer ("did X confirm?", "when is the meeting?"). 1–3 lines. Lead with the answer, then the supporting message.

- "status_rundown": the question asks what's open/pending/blocked. Group by chat group. Action items first, FYIs last.

- "per_thread_digest": broad "what's happening with X" questions. One short block per thread, group name as the header.

- "timeline": "how did this unfold / what's the history" questions. Chronological, dates in `monospace`.

- "clarify": the retrieved context matches two different people, projects, or interpretations and answering would require guessing which one the user means. Ask ONE short question naming the options. Do not attempt a partial answer.

- "not_found": the messages don't address the question. Say so plainly, state what WAS searched (use the search stats), and suggest one concrete refinement. Never pad with tangentially related content to seem useful.

═══ STYLE ═══

- WhatsApp markdown: *bold* key terms, `monospace` dates/numbers, _italics_ sparingly.
- Crisp. Every line earns its place. No preamble like "Based on your messages..."
- Warm but professional — you're their assistant, not a report generator.
- If nothing is urgent, say so in one line. Don't invent a crisis.

End the answer with one footer line:
_Searched {n_searched} groups · {n_matched} relevant · {n_messages} messages used_

Respond with JSON only:
{{
  "response_type": "...",
  "answer": "the full WhatsApp-formatted answer text"
}}"""


# ── Freeform prompt ───────────────────────────────────────────────────────────

FREEFORM_PROMPT = """You are Mosaic, a concise personal assistant on WhatsApp. Respond to the user's message directly and helpfully.

Recent conversation:
{transcript}

User's new message: "{message}"

Rules:
- Be brief. 2-4 short paragraphs max. No fluff.
- Use WhatsApp markdown: *bold* for key terms, _italic_ for emphasis.
- If the user asks a factual question, answer it directly.
- If the user shares info, acknowledge and offer relevant next steps.
- If you don't know something, say so honestly.
- Reference earlier conversation context naturally when relevant.

Your response (plain text, no JSON):"""


# ── Public API ────────────────────────────────────────────────────────────────

def run_pipeline(message: str, transcript_text: str, progress=None) -> str:
    def _say(msg: str):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    _say("Analyzing…")
    plan = _query_prep(message, transcript_text)
    print(f"[pipeline] intent={plan.intent} query=\"{plan.standalone_query[:60]}\" "
          f"terms={plan.search_terms[:8]} window={plan.time_window_days}d")

    msg_lower = message.strip().lower()
    if msg_lower in ("help", "what can you do", "what do you do"):
        return HELP_TEXT

    if plan.intent == "greeting":
        return random.choice(GREETINGS)

    if plan.intent == "freeform":
        return _respond_freeform(message, transcript_text)

    if plan.intent in ("cron_setup", "cron_manage"):
        return _route_cron(plan, message)

    if plan.intent == "status_check":
        from hermes_bot.cron.searcher import run_status_check
        return run_status_check(progress=progress)

    if plan.intent == "person" and plan.person_name:
        contact = db.get_best_contact(plan.person_name)
        if not contact:
            contact = _resolve_person_from_chats(plan.person_name)
        if not contact:
            return f"I couldn't find *{plan.person_name}* in your contacts. Try a different name?"
        plan.person_jid = contact["jid"]

    # ── search / person: full 5-stage pipeline ──
    _say(f"Searching your chats for \"{plan.standalone_query[:60]}\"…")
    matches = _keyword_search(plan)

    if not matches:
        if plan.time_window_days < 30:
            _say("Widening search window…")
            plan.time_window_days = 30
            matches = _keyword_search(plan)
        if not matches:
            return (
                f"I searched your chats for *{plan.standalone_query}* but found nothing.\n"
                f"Try different keywords or a broader time frame."
            )

    total_hits = sum(m.hit_count for m in matches)
    _say(f"{len(matches)} groups matched ({total_hits} hits) — filtering relevant messages…")

    results = _refine_groups(plan, matches, progress)
    active = [r for r in results if r.relevant_messages]

    if not active:
        return (
            f"Found keyword matches in {len(matches)} groups but none were actually "
            f"about *{plan.standalone_query}*.\n"
            f"The matches may have been coincidental. Try rephrasing?"
        )

    context_text, stats = _assemble_context(plan, results)

    n_msgs = stats["n_messages"]
    n_matched = stats["n_matched"]
    _say(f"Composing answer from {n_msgs} messages across {n_matched} groups…")
    answer = _generate_answer(plan, context_text, stats)

    return answer


# ── Stage 1: Query Prep ──────────────────────────────────────────────────────

def _query_prep(message: str, transcript_text: str) -> QueryPlan:
    contact_names = _get_relevant_contacts(message)

    prompt = STAGE1_PROMPT.format(
        transcript=transcript_text or "(no prior conversation)",
        contact_names=contact_names or "(none available)",
        message=message,
    )

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL_FAST,
            contents=prompt,
            config={"response_mime_type": "application/json", "temperature": 0},
        )
        data = json.loads(response.text)
        return QueryPlan(
            intent=data.get("intent", "freeform"),
            standalone_query=data.get("standalone_query", message),
            entities=data.get("entities", []),
            search_terms=data.get("search_terms", []),
            time_window_days=data.get("time_window_days", 14),
            scope=data.get("scope", "all_groups"),
            group_hint=data.get("group_hint", ""),
            person_name=data.get("person_name", ""),
            cron_details=data.get("cron_details", {}),
        )
    except Exception as e:
        print(f"[pipeline:stage1] Error: {e}")
        return QueryPlan(intent="freeform", standalone_query=message)


# ── Stage 2: Keyword Search ───────────────────────────────────────────────────

def _keyword_search(plan: QueryPlan, min_term_matches: int = 1) -> list[GroupMatch]:
    if not plan.search_terms:
        return []

    threshold = (datetime.now(timezone.utc) - timedelta(days=plan.time_window_days)).isoformat()

    scope_where, scope_params = _build_scope_filter(plan)
    if scope_where is None:
        return []

    terms_lower = [t.lower() for t in plan.search_terms]

    if min_term_matches <= 1:
        like_clause = " OR ".join(["LOWER(m.content) LIKE ?" for _ in terms_lower])
        term_condition = f"({like_clause})"
        term_params = [f"%{t}%" for t in terms_lower]
    else:
        case_parts = [f"CASE WHEN LOWER(m.content) LIKE ? THEN 1 ELSE 0 END" for _ in terms_lower]
        count_expr = " + ".join(case_parts)
        term_condition = f"({count_expr}) >= {min_term_matches}"
        term_params = [f"%{t}%" for t in terms_lower]

    conn = db._connect()
    query = f"""
        SELECT m.rowid AS msg_id, m.chat_jid, m.timestamp, ch.name AS chat_name
        FROM messages m
        JOIN chats ch ON m.chat_jid = ch.jid
        LEFT JOIN wa.whatsmeow_chat_settings cs ON m.chat_jid = cs.chat_jid
        WHERE (cs.archived IS NULL OR cs.archived = 0)
          {scope_where}
          AND m.timestamp >= ?
          AND m.content IS NOT NULL
          AND m.content != ''
          AND {term_condition}
        ORDER BY m.timestamp DESC
        LIMIT 500
    """
    rows = conn.execute(query, scope_params + [threshold] + term_params).fetchall()
    conn.close()

    group_data: dict[str, dict] = {}
    for row in rows:
        jid = row["chat_jid"]
        if jid not in group_data:
            group_data[jid] = {
                "ids": [],
                "name": row["chat_name"] or jid.split("@")[0],
                "latest": row["timestamp"],
            }
        group_data[jid]["ids"].append(row["msg_id"])
        ts = row["timestamp"]
        if ts and ts > group_data[jid]["latest"]:
            group_data[jid]["latest"] = ts

    now = datetime.now(timezone.utc)
    matches = []
    for jid, gd in group_data.items():
        hits = len(gd["ids"])
        try:
            last_ts = datetime.fromisoformat(gd["latest"].replace(" ", "T"))
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            days_ago = max(0, (now - last_ts).days)
        except (ValueError, TypeError):
            days_ago = 7
        score = hits * (1.0 / (1.0 + days_ago))
        matches.append(GroupMatch(
            group_jid=jid,
            group_name=gd["name"],
            matched_ids=gd["ids"],
            hit_count=hits,
            score=score,
        ))

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:6]


def _build_scope_filter(plan: QueryPlan) -> tuple[str, list]:
    if plan.scope == "person" and plan.person_jid:
        return "AND m.chat_jid = ?", [plan.person_jid]
    if plan.scope == "specific_group" and plan.group_hint:
        hint = plan.group_hint.lower()
        conn = db._connect()
        rows = conn.execute(
            "SELECT jid FROM chats WHERE LOWER(name) LIKE ? AND jid LIKE '%@g.us' LIMIT 1",
            (f"%{hint}%",),
        ).fetchall()
        conn.close()
        if rows:
            return "AND m.chat_jid = ?", [rows[0]["jid"]]
        all_groups = db.get_non_archived_groups()
        matching = [g for g in all_groups if hint in g["name"].lower()]
        if matching:
            jids = [g["jid"] for g in matching[:3]]
            placeholders = ",".join("?" for _ in jids)
            return f"AND m.chat_jid IN ({placeholders})", jids
        return None, []
    return "AND m.chat_jid LIKE '%@g.us'", []


# ── Stage 3: Refine ──────────────────────────────────────────────────────────

def _refine_groups(plan: QueryPlan, matches: list[GroupMatch], progress=None) -> list[RefineResult]:
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_refine_single_group, plan, m): m for m in matches}
        for future in concurrent.futures.as_completed(futures):
            match = futures[future]
            try:
                result = future.result()
                if result.relevant_messages:
                    if progress:
                        try:
                            progress(f"✓ {result.group_name}: {len(result.relevant_messages)} relevant msgs")
                        except Exception:
                            pass
                results.append(result)
            except Exception as e:
                print(f"[pipeline:stage3] {match.group_name} failed: {e}")
                results.append(RefineResult(
                    group_jid=match.group_jid,
                    group_name=match.group_name,
                ))
    return results


def _refine_single_group(plan: QueryPlan, match: GroupMatch) -> RefineResult:
    all_msgs = _get_group_messages_with_ids(match.group_jid, days=plan.time_window_days)

    if not all_msgs:
        return RefineResult(group_jid=match.group_jid, group_name=match.group_name)

    matched_set = set(match.matched_ids)
    matched_indices = [i for i, m in enumerate(all_msgs) if m["msg_id"] in matched_set]

    if not matched_indices:
        return RefineResult(group_jid=match.group_jid, group_name=match.group_name)

    expanded: set[int] = set()
    for idx in matched_indices:
        for offset in range(-5, 6):
            i = idx + offset
            if 0 <= i < len(all_msgs):
                expanded.add(i)

    context_msgs = [all_msgs[i] for i in sorted(expanded)]

    numbered_lines = []
    for i, msg in enumerate(context_msgs, 1):
        ts = msg["time"]
        ts_str = ts.strftime("%m/%d %H:%M") if isinstance(ts, datetime) else str(ts)
        sender = msg.get("sender", "Unknown")
        content = msg.get("content", "")
        numbered_lines.append(f"[{i}] [{ts_str}] {sender}: {content}")

    entities_str = json.dumps(plan.entities) if plan.entities else "[]"

    prompt = STAGE3_PROMPT.format(
        standalone_query=plan.standalone_query,
        entities=entities_str,
        group_name=match.group_name,
        messages="\n".join(numbered_lines),
    )

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL_FAST,
            contents=prompt,
            config={"response_mime_type": "application/json", "temperature": 0},
        )
        data = json.loads(response.text)

        def _msg_by_num(num: int) -> dict | None:
            if 1 <= num <= len(context_msgs):
                return context_msgs[num - 1]
            return None

        relevant_msgs = []
        for rid in data.get("relevant_ids", []):
            m = _msg_by_num(rid)
            if m:
                relevant_msgs.append(m)

        threads_out = []
        for t in data.get("threads", []):
            thread_msgs = []
            for mid in t.get("msg_ids", []):
                m = _msg_by_num(mid)
                if m:
                    thread_msgs.append(m)
            if thread_msgs:
                threads_out.append({"label": t.get("label", ""), "messages": thread_msgs})

        return RefineResult(
            group_jid=match.group_jid,
            group_name=match.group_name,
            entity_match=data.get("entity_match", False),
            threads=threads_out,
            relevant_messages=relevant_msgs,
        )
    except Exception as e:
        print(f"[pipeline:stage3] {match.group_name}: {e}")
        return RefineResult(group_jid=match.group_jid, group_name=match.group_name)


# ── Stage 4: Assemble Context ─────────────────────────────────────────────────

def _assemble_context(plan: QueryPlan, results: list[RefineResult]) -> tuple[str, dict]:
    active = [r for r in results if r.relevant_messages]

    blocks = []
    total_msgs = 0

    for result in active:
        if result.threads:
            for thread in result.threads:
                if not thread["messages"]:
                    continue
                header = _format_thread_header(result.group_name, thread["messages"])
                body = "\n".join(
                    f"[{m['time'].strftime('%m/%d %H:%M')}] {m['sender']}: {m['content']}"
                    for m in thread["messages"]
                )
                blocks.append(f"{header}\n{body}")
                total_msgs += len(thread["messages"])
        else:
            header = _format_thread_header(result.group_name, result.relevant_messages)
            body = "\n".join(
                f"[{m['time'].strftime('%m/%d %H:%M')}] {m['sender']}: {m['content']}"
                for m in result.relevant_messages
            )
            blocks.append(f"{header}\n{body}")
            total_msgs += len(result.relevant_messages)

    context_text = "\n\n---\n\n".join(blocks)
    stats = {
        "n_searched": len(results),
        "n_matched": len(active),
        "n_messages": total_msgs,
        "matched_group_names": [r.group_name for r in active],
    }
    return context_text, stats


def _format_thread_header(group_name: str, messages: list) -> str:
    participants = list(dict.fromkeys(m["sender"] for m in messages))
    times = [m["time"] for m in messages if m["time"]]
    if times:
        start = min(times).strftime("%b %d")
        end = max(times).strftime("%b %d")
        date_range = start if start == end else f"{start} – {end}"
    else:
        date_range = "unknown"
    return (
        f"═══ GROUP: {group_name} | "
        f"PARTICIPANTS: {', '.join(participants)} | "
        f"DATES: {date_range} ═══"
    )


# ── Stage 5: Generate Answer ──────────────────────────────────────────────────

def _generate_answer(plan: QueryPlan, context_text: str, stats: dict) -> str:
    matched_names = ", ".join(stats["matched_group_names"]) if stats["matched_group_names"] else "none"

    prompt = STAGE5_PROMPT.format(
        standalone_query=plan.standalone_query,
        n_searched=stats["n_searched"],
        n_matched=stats["n_matched"],
        matched_group_names=matched_names,
        n_messages=stats["n_messages"],
        context_package=context_text,
    )

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL_FAST,
            contents=prompt,
            config={"response_mime_type": "application/json", "temperature": 0.2},
        )
        data = json.loads(response.text)
        response_type = data.get("response_type", "unknown")
        answer = data.get("answer", "I could not generate a response.")
        print(f"[pipeline:stage5] response_type={response_type}")
        return answer
    except Exception as e:
        print(f"[pipeline:stage5] Error: {e}")
        return "I hit an error generating the answer. Try again."


# ── Freeform handler ──────────────────────────────────────────────────────────

def _respond_freeform(message: str, transcript_text: str) -> str:
    msg_stripped = message.strip().lower()
    if msg_stripped in ("help", "what can you do", "what do you do"):
        return HELP_TEXT

    prompt = FREEFORM_PROMPT.format(
        transcript=transcript_text or "(start of conversation)",
        message=message,
    )

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL_FAST,
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        print(f"[pipeline:freeform] Error: {e}")
        return "Got it. What would you like me to help with?"


# ── Cron routing ──────────────────────────────────────────────────────────────

def _route_cron(plan: QueryPlan, message: str) -> str:
    from hermes_bot.cron.feedback import (
        handle_cron_setup, handle_cron_list, handle_cron_manage,
        handle_feedback, handle_keep,
    )

    if plan.intent == "cron_setup":
        return handle_cron_setup(plan.cron_details)

    msg_lower = message.strip().lower()
    if any(w in msg_lower for w in ("list", "show", "see")):
        return handle_cron_list()
    if "pause" in msg_lower:
        return handle_cron_manage("pause", message)
    if "resume" in msg_lower:
        return handle_cron_manage("resume", message)
    if any(w in msg_lower for w in ("delete", "remove")):
        return handle_cron_manage("delete", message)
    if "feedback" in msg_lower:
        return handle_feedback(message)
    if any(w in msg_lower for w in ("keep", "like", "save", "good")):
        return handle_keep()

    return handle_cron_list()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_relevant_contacts(message: str) -> str:
    words = message.split()
    potential_names = [
        w.strip(".,!?;:") for w in words
        if w and w[0].isupper() and len(w) > 2
    ]
    if not potential_names:
        return ""

    seen = set()
    names = []
    for hint in potential_names:
        hl = hint.lower()
        if hl in seen:
            continue
        seen.add(hl)
        for c in db.resolve_contact_by_name(hint):
            if c["name"] and c["name"] not in names:
                names.append(c["name"])

    return ", ".join(names[:20]) if names else ""


def _resolve_person_from_chats(name_hint: str) -> dict | None:
    conn = db._connect()
    row = conn.execute(
        "SELECT jid, name FROM chats WHERE name LIKE ? AND jid NOT LIKE '%@g.us' ORDER BY last_message_time DESC LIMIT 1",
        (f"%{name_hint}%",),
    ).fetchone()
    conn.close()
    if row:
        return {"jid": row["jid"], "name": row["name"]}
    return None


def _get_group_messages_with_ids(chat_jid: str, days: int = 14, limit: int = 200) -> list[dict]:
    threshold = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    conn = db._connect()
    rows = conn.execute("""
        SELECT m.rowid AS msg_id, m.content, m.timestamp, m.is_from_me,
               COALESCE(c.full_name, c.push_name, c.first_name, c.business_name) AS contact_name,
               ms.sender_jid,
               ch.name AS chat_name
        FROM messages m
        LEFT JOIN chats ch ON m.chat_jid = ch.jid
        LEFT JOIN wa.whatsmeow_message_secrets ms
            ON m.id = ms.message_id AND m.chat_jid = ms.chat_jid
        LEFT JOIN wa.whatsmeow_contacts c ON ms.sender_jid = c.their_jid
        WHERE m.chat_jid = ?
          AND m.timestamp >= ?
          AND m.content IS NOT NULL
          AND m.content != ''
        ORDER BY m.timestamp ASC
        LIMIT ?
    """, (chat_jid, threshold, limit)).fetchall()
    conn.close()

    results = []
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["timestamp"].replace(" ", "T"))
        except (ValueError, AttributeError):
            dt = datetime.now(timezone.utc)

        sender = "You" if r["is_from_me"] else (
            r["contact_name"]
            or (r["sender_jid"].split("@")[0] if r["sender_jid"] else "Unknown")
        )

        results.append({
            "msg_id": r["msg_id"],
            "time": dt,
            "sender": sender,
            "content": r["content"].strip(),
            "is_from_me": bool(r["is_from_me"]),
            "chat_name": r["chat_name"] or chat_jid.split("@")[0],
        })
    return results
