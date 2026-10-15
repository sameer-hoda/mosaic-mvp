#!/usr/bin/env python3
"""
evaluate_models.py
──────────────────
Automated Evaluation Harness for wa-slash-commands.
Benchmarking different Gemini/Gemma models against real chat history.
"""

import os
import sys
import json
import time
import sqlite3
import hashlib
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

import google.generativeai as genai
from dotenv import load_dotenv

# ── Setup ─────────────────────────────────────────────────────────────────────
_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_dir, ".env"), override=True)

API_KEY = os.getenv("GEMINI_API_KEY")
MESSAGES_DB = os.getenv("MESSAGES_DB_PATH", os.path.join(_dir, "store", "messages.db"))

if not API_KEY:
    print("❌ Error: GEMINI_API_KEY not found in .env")
    sys.exit(1)

genai.configure(api_key=API_KEY)

# ── Competitors ───────────────────────────────────────────────────────────────
MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemma-4-31b-it"
]

JUDGE_MODEL = "gemini-3.1-pro-preview"

# ── Data Miner ────────────────────────────────────────────────────────────────
def find_test_group() -> Optional[str]:
    """Find the chat_jid with the most messages in the last 30 days."""
    if not os.path.exists(MESSAGES_DB):
        print(f"❌ Database not found at {MESSAGES_DB}")
        return None
    
    conn = sqlite3.connect(MESSAGES_DB)
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    query = """
        SELECT chat_jid, COUNT(*) as msg_count 
        FROM messages 
        WHERE timestamp > ? AND chat_jid LIKE '%@g.us'
        GROUP BY chat_jid 
        ORDER BY msg_count DESC 
        LIMIT 1
    """
    res = conn.execute(query, (cutoff,)).fetchone()
    conn.close()
    return res[0] if res else None

def load_context(jid: str, limit: int = 3000) -> str:
    """Load up to `limit` messages from the given JID."""
    conn = sqlite3.connect(MESSAGES_DB)
    query = """
        SELECT sender, content, timestamp 
        FROM messages 
        WHERE chat_jid = ? AND content IS NOT NULL AND content != ''
        ORDER BY timestamp DESC 
        LIMIT ?
    """
    rows = conn.execute(query, (jid, limit)).fetchall()
    conn.close()
    
    lines = []
    # Reverse to get chronological order [oldest -> newest] for context
    for sender, content, ts in reversed(rows):
        lines.append(f"[{ts}] {sender}: {content}")
    return "\n".join(lines)

# ── The Gauntlet ──────────────────────────────────────────────────────────────
SOTU_PROMPT_TEMPLATE = """You are a sharp executive chief of staff. Produce a 'State of the Union' summary for this group chat.

Chat History:
{context}

Return ONLY valid JSON with these exact fields:
- "group_purpose": What this group is about (short paragraph)
- "big_picture_objective": The big picture objective (short paragraph)
- "key_themes": array of objects, each with "title" and "description"
- "recent_happenings": What's been happening recently (last 2 weeks)
- "key_ask": The one thing asked for recently

Output ONLY valid JSON, no markdown.
"""

PENDING_PROMPT_TEMPLATE = """You are an executive chief of staff. Review this chat history. Identify the ongoing projects, tasks, or distinct work streams (initiatives). For each initiative, extract the most important chronological developments, decisions, or roadblocks. Finally, synthesize a clear, single-line "Next Step" that specifies who needs to do what. Be extremely thorough.

Chat History:
{context}

Return ONLY valid JSON with:
- "initiatives": array of objects, each with:
    "title": string (Name of the initiative),
    "updates": array of strings,
    "next_steps": string (A line what is expected next)

Output ONLY valid JSON, no markdown.
"""

def run_test(model_name: str, prompt: str, schema: dict) -> dict:
    """Run a single model test and return results."""
    t0 = time.time()
    result = {
        "status": "Success",
        "latency": 0.0,
        "payload_chars": len(prompt),
        "output": "",
        "error": ""
    }
    
    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": schema
            }
        )
        result["output"] = response.text
    except Exception as e:
        result["status"] = "Failed"
        result["error"] = str(e)
    
    result["latency"] = time.time() - t0
    return result

# ── The Judge ─────────────────────────────────────────────────────────────────
def judge_output(original_context: str, model_output: str, command: str) -> dict:
    """Use the Judge model to grade the competitor's output."""
    prompt = f"""You are an elite AI Evaluator. Grade the following AI-generated WhatsApp {command} summary based on the provided raw chat context.

Original Chat Context (excerpt):
{original_context[:20000]} ... (truncated for judge)

AI-Generated Output to Evaluate:
{model_output}

Grade the output on a scale of 1-10 for:
1. Hallucinations: Does it invent facts not in the context? (10 = No hallucinations)
2. Completeness: Did it miss major projects or decisions discussed? (10 = Very complete)
3. Formatting: Is the JSON/Structure perfect? (10 = Perfect)

Return ONLY valid JSON:
{{
  "hallucination_score": int,
  "completeness_score": int,
  "formatting_score": int,
  "total_score": float,
  "feedback": "string"
}}
"""
    try:
        judge = genai.GenerativeModel(JUDGE_MODEL)
        response = judge.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except:
        return {"total_score": 0, "feedback": "Judge failed."}

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n🚀 {C.BOLD}Starting Model Evaluation Harness{C.RESET}")
    print("────────────────────────────────────────────────────────")
    
    jid = find_test_group()
    if not jid:
        print("❌ No active group chats found to test with.")
        return
    
    print(f"📊 Targeted Group: {C.CYAN}{jid}{C.RESET}")
    print("📚 Loading context (limit 3,000 messages)...")
    context = load_context(jid)
    print(f"✅ Loaded {len(context):,} characters of context.\n")
    
    report_card = []
    
    sotu_schema = {
        "type": "object",
        "properties": {
            "group_purpose": {"type": "string"},
            "big_picture_objective": {"type": "string"},
            "key_themes": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"}}}}
        }
    }
    
    for model_name in MODELS:
        print(f"🧪 Testing {C.BOLD}{model_name}{C.RESET}...")
        
        # Test SOTU
        prompt = SOTU_PROMPT_TEMPLATE.format(context=context)
        res = run_test(model_name, prompt, sotu_schema)
        
        score = 0
        if res["status"] == "Success":
            print(f"   ✅ Done ({res['latency']:.1f}s). Grading...")
            grading = judge_output(context, res["output"], "/sotu")
            score = grading.get("total_score", 0)
        else:
            print(f"   ❌ Failed: {res['error'][:60]}...")
            
        report_card.append({
            "model": model_name,
            "status": res["status"],
            "latency": f"{res['latency']:.1f}s",
            "score": score
        })
        print(f"   ⭐ Judge Score: {score}/10\n")
        time.sleep(5) # Avoid hitting rate limits

    # ── Final Report ──────────────────────────────────────────────────────────
    print("\n" + "═"*65)
    print(f" {'MODEL':<25} | {'STATUS':<10} | {'LATENCY':<8} | {'SCORE':<5}")
    print(" " + "─"*63)
    for r in report_card:
        color = C.GREEN if r["status"] == "Success" else C.RED
        print(f" {r['model']:<25} | {color}{r['status']:<10}{C.RESET} | {r['latency']:<8} | {r['score']:<5}")
    print("═"*65 + "\n")

class C:
    BOLD   = "\033[1m"
    GREEN  = "\033[92m"
    CYAN   = "\033[96m"
    RED    = "\033[91m"
    RESET  = "\033[0m"

if __name__ == "__main__":
    main()
