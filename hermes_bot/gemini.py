"""Gemini Flash-Lite client for extraction.

Model: gemini-3.5-flash-lite (override via GEMINI_MODEL env).
Key: env GEMINI_API_KEY first, else the wizard-saved key (live re-read).
The key is NEVER printed, logged, or
stored — tests use fake keys and event_log rows carry only the model
name plus token counts.
"""

import json
import os
import re

MODEL_DEFAULT = "gemini-3.5-flash-lite"

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string",
                             "enum": ["ask", "commitment", "blocker", "decision"]},
                    "what": {"type": "string"},
                    "context": {"type": "string"},
                    "owner": {"type": "string"},
                    "requester": {"type": "string"},
                    "due": {"type": "string"},
                    "status": {"type": "string",
                               "enum": ["open", "done", "blocked"]},
                    "topic": {"type": "string"},
                },
                "required": ["kind", "what", "status"],
            },
        }
    },
    "required": ["items"],
}

PROMPT_TMPL = """You extract ACTION ITEMS from a WhatsApp chat for the owner's personal tracker.

Chat: {chat} ({n} messages, {start} to {end}).
---
{body}
---

Return ONLY JSON matching the schema: {{"items": [...]}} — one object per
DISTINCT action item (max 4), most important first. Empty list [] if the
thread is pure banter, greetings, FYI links, or logistics with no ask.

Item kinds: ask (someone asked another person to do/answer something),
commitment (someone promised to do something), blocker (work explicitly
stuck), decision (a decision requiring follow-through).
"what" must be a clean verb-first instruction, NEVER a raw quote.
"done" ONLY if the chat explicitly confirms completion; else "open".
"""


def model_name() -> str:
    return os.getenv("GEMINI_MODEL", MODEL_DEFAULT)


def get_api_key() -> str:
    """Env wins; falls back to the wizard-saved key, re-read live."""
    from hermes_bot import config as _config
    return os.getenv("GEMINI_API_KEY", "") or _config.read_saved_gemini_key()


def _build_client(api_key: str):
    from google import genai
    return genai.Client(api_key=api_key)


def _usage_counts(resp) -> tuple[int, int]:
    usage = getattr(resp, "usage_metadata", None)
    if not usage:
        return 0, 0
    in_tok = getattr(usage, "prompt_token_count", 0) or 0
    out_tok = getattr(usage, "candidates_token_count", 0) or 0
    if not out_tok:
        out_tok = getattr(usage, "total_token_count", 0) or 0
    return int(in_tok), int(out_tok)


def _log_call(store_conn, model: str, in_tok: int, out_tok: int, detail: str = "") -> None:
    if store_conn is None:
        return
    from hermes_bot import store as _store
    _store.log_event(store_conn, "gemini_call", model=model,
                     input_tokens=in_tok, output_tokens=out_tok, detail=detail)


def extract_items(thread_text: str, chat: str = "", start: str = "",
                  end: str = "", n: int = 0, model: str | None = None,
                  client=None, store_conn=None,
                  api_key: str | None = None) -> dict:
    """Run one Flash-Lite extraction call with strict JSON schema output.

    Every call logs model + input/output tokens to event_log (via
    store_conn). `client` is injectable so tests never touch the network.
    Fail-open: any error returns {"items": []} after logging the failure.
    """
    model = model or model_name()
    prompt = PROMPT_TMPL.format(
        chat=chat or "unknown", n=n or thread_text.count("\n") + 1,
        start=start or "?", end=end or "?", body=thread_text[:6000],
    )
    try:
        if client is None:
            key = api_key if api_key is not None else get_api_key()
            if not key:
                raise RuntimeError("GEMINI_API_KEY is not set")
            client = _build_client(key)
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": ITEM_SCHEMA,
            },
        )
        in_tok, out_tok = _usage_counts(resp)
        _log_call(store_conn, model, in_tok, out_tok)
        text = resp.text.strip()
        m = re.search(r"\{.*\}", text, re.S)
        raw = json.loads(m.group(0)).get("items") or []
        return {"items": list(raw[:4])}
    except Exception as e:
        _log_call(store_conn, model, 0, 0, detail=f"error:{type(e).__name__}")
        return {"items": []}
