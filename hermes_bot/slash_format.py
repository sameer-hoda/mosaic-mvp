#!/usr/bin/env python3
"""WA message templates for private slash-command answers (Worker C)."""

from typing import Sequence


def format_answer(command: str, chat_name: str, messages: Sequence[str],
                  arg: str = "") -> str:
    if command == "catchup":
        lines = [f"*Catchup — {chat_name}*", ""]
        recent = list(messages)[-10:]
        if not recent:
            lines.append("No recent messages found.")
        else:
            lines.extend(f"• {m}" for m in recent)
        return "\n".join(lines).strip()
    if command == "pending":
        open_loops = [m for m in messages
                      if ("?" in m or "todo" in m.lower()
                          or "please" in m.lower() or "pending" in m.lower())]
        if not open_loops:
            return f"✅ *No critical open loops found — {chat_name}.*"
        lines = [f"*Pending — {chat_name}*", ""]
        lines.extend(f"• {m}" for m in open_loops[-10:])
        return "\n".join(lines).strip()
    if command == "sotu":
        lines = [f"*State of the Union — {chat_name}*", ""]
        lines.append(f"{len(messages)} messages in scope.")
        recent = list(messages)[-5:]
        if recent:
            lines.append("")
            lines.append("*Recent signals*")
            lines.extend(f"• {m}" for m in recent)
        return "\n".join(lines).strip()
    if command == "eli5":
        topic = (arg or "").strip()
        if not topic:
            return "Give me a topic: `/eli5 <topic>`."
        lines = [f"*ELI5: {topic}*", ""]
        lines.append(f"In plain words: {topic} — explained simply.")
        if messages:
            lines.append("")
            lines.append(f"(Context: {len(messages)} messages from {chat_name}.)")
        return "\n".join(lines).strip()
    return ""


def format_public_preview(text: str, chat_name: str) -> str:
    return (
        "🔒 *Public-post preview — not sent yet*\n"
        f"Chat: {chat_name}\n\n"
        f"{text}\n\n"
        'Reply `yes` within 5 minutes to post it to the group.'
    )
