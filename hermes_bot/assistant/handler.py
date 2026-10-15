import json

from google import genai

from hermes_bot import config
from hermes_bot.assistant.session import Session

INTENT_PROMPT = """You are a personal assistant routing engine. Classify the user's message.

Message: "{message}"

Respond with JSON only:
{{
  "intent": "ask" | "cron_setup" | "cron_list" | "cron_pause" | "cron_resume" |
            "cron_delete" | "cron_feedback" | "cron_keep" | "status_check" |
            "person" | "question" | "statement" | "help" | "greeting" | "unknown",
  "query": "the extracted query or topic if applicable",
  "needs_context": true/false,
  "context_scope": "mechat_only" | "all_groups" | "specific_group",
  "group_hint": "partial group name if specific_group",
  "person_name": "extracted person name if intent is person",
  "cron_details": {{
    "query": "",
    "frequency": "daily" | "weekdays" | "weekly" | "oneshot",
    "time_slot": "HH:MM"
  }}
}}

Intent guide:
- "ask": user wants a search/summary across groups. Messages like "what's happening with X", "find updates on Y", "/ask Z"
- "cron_setup": user wants to schedule recurring summaries. Messages like "send me X every morning", "/cron add", "summarize Y daily at 9"
- "cron_list"/"cron_pause"/"cron_resume"/"cron_delete": explicit cron management
- "cron_feedback": user giving feedback on a summary. Messages like "this was too broad", "focus on decisions only"
- "cron_keep": user wants to keep a cron job. Messages like "keep this", "like it", "save this"
- "status_check": user wants a personal status report — what's open across all groups that needs their attention. Messages like "what's open", "what's pending on me", "catch me up", "any blockers", "what did I miss", "sitrep", "what needs my attention". NOT for topic-specific queries with a named subject like "what's happening with X".
- "person": user is asking about a specific person. Messages like "what are chats with Miten", "updates from Rachit", "what's happening with Akshay", "what did Pallavi say", "catch me up on Mayank"
- "question": user asking something that might need context
- "statement": user making a statement or sharing info
- "help": user asking for help
- "greeting": casual hello/hi
"""


class Intent:
    def __init__(
        self,
        intent: str = "unknown",
        query: str = "",
        needs_context: bool = False,
        context_scope: str = "mechat_only",
        group_hint: str = "",
        person_name: str = "",
        cron_details: dict = None,
    ):
        self.intent = intent
        self.query = query
        self.needs_context = needs_context
        self.context_scope = context_scope
        self.group_hint = group_hint
        self.person_name = person_name
        self.cron_details = cron_details or {}


def detect_intent(session: Session, message: str) -> Intent:
    if message.strip().lower() in ("/help", "help"):
        return Intent(intent="help")

    client = genai.Client(api_key=config.GEMINI_API_KEY)

    prompt = INTENT_PROMPT.format(message=message)

    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL_FAST,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        data = json.loads(response.text)
        return Intent(
            intent=data.get("intent", "unknown"),
            query=data.get("query", message),
            needs_context=data.get("needs_context", False),
            context_scope=data.get("context_scope", "mechat_only"),
            group_hint=data.get("group_hint", ""),
            person_name=data.get("person_name", ""),
            cron_details=data.get("cron_details", {}),
        )
    except Exception as e:
        print(f"[intent] Error: {e}")
        return Intent(intent="question", query=message, needs_context=False)
