"""
ops/llm.py — Gemini wrapper using google-genai (new SDK)
Independent from engine.py (which uses old google-generativeai)
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_ops_dir = Path(__file__).resolve().parent
_env_path = _ops_dir.parent / ".env"
load_dotenv(_env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"

from google import genai
from google.genai import types

_client = None
if GEMINI_API_KEY:
    _client = genai.Client(api_key=GEMINI_API_KEY, http_options={"timeout": 600_000})


def gemini_json(prompt: str, fallback: dict, schema: Optional[dict] = None) -> dict:
    """Call Gemini with JSON response. Retries once."""
    if not _client:
        return fallback

    config = types.GenerateContentConfig(
        temperature=0.1,
        response_mime_type="application/json",
    )
    if schema:
        config.response_schema = schema

    for attempt in range(2):
        try:
            resp = _client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
            text = resp.text if hasattr(resp, "text") else ""
            if not text and hasattr(resp, "candidates") and resp.candidates:
                text = resp.candidates[0].content.parts[0].text
            import json
            return json.loads(text) if text else fallback
        except Exception as e:
            print(f"⚠️ [LLM] Gemini error (attempt {attempt+1}): {e}")
            if attempt == 0:
                continue
    return fallback

    for attempt in range(2):
        try:
            resp = _client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            text = resp.text if hasattr(resp, "text") else ""
            if not text and hasattr(resp, "candidates") and resp.candidates:
                text = resp.candidates[0].content.parts[0].text
            import json
            return json.loads(text) if text else fallback
        except Exception as e:
            print(f"⚠️ [LLM] Gemini error (attempt {attempt+1}): {e}")
            if attempt == 0:
                continue
    return fallback


def gemini_text(prompt: str, max_tokens: int = 4096) -> str:
    """Freeform text generation."""
    if not _client:
        return "AI unavailable."
    try:
        resp = _client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=max_tokens,
            ),
        )
        return resp.text.strip()
    except Exception as e:
        return f"AI Error: {e}"
