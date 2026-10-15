#!/usr/bin/env python3
"""
formatter.py
──────────────────────
All WA message templates for slash command V2.
"""

def fmt_sotu(data: dict) -> str:
    lines = ["*State of the Union*\n"]
    
    if data.get("group_purpose"):
        lines.append("*What this group is about*")
        lines.append(data["group_purpose"])
        lines.append("")
        
    if data.get("big_picture_objective"):
        lines.append("*The big picture objective*")
        lines.append(data["big_picture_objective"])
        lines.append("")
        
    themes = data.get("key_themes", [])
    if themes:
        lines.append("*Key discussion themes*")
        for t in themes:
            lines.append(f"• *{t.get('title', '')}* — {t.get('description', '')}")
        lines.append("")
        
    if data.get("recent_happenings"):
        lines.append("*What's been happening recently*")
        lines.append(data["recent_happenings"])
        lines.append("")
        
    if data.get("key_ask"):
        lines.append("*The one thing asked for recently*")
        lines.append(data["key_ask"])
        
    return "\n".join(lines).strip()

def fmt_pending(data: dict) -> str:
    initiatives = data.get("initiatives", [])
    if not initiatives:
        return "✅ *No critical open loops found.*"
    
    messages = []
    for init in initiatives:
        lines = [f"🎯 {init.get('title', 'Initiative')}"]
        lines.append("")
        for upd in init.get("updates", []):
            lines.append(f"📅 {upd}")
        lines.append("")
        lines.append(f"⏭ *Next:* {init.get('next_steps', '')}")
        messages.append("\n".join(lines))
        
    return "\n\n────────────────────\n\n".join(messages)

def fmt_stats(data: dict, days: int = 14) -> str:
    participants = data.get("participants", [])
    health = data.get("group_health", "")
    bottleneck = data.get("bottleneck", "None")

    lines = [f"📊 *Team Stats — Last {days} Days*", ""]
    
    for p in participants[:5]:
        name = p.get("name", "Unknown")
        count = p.get("message_count", 0)
        pct = p.get("share_pct", 0)
        role = p.get("role_tag", "")
        lines.append(f"👤 *{name}* ({count} msgs, {pct:.0f}%) — {role}")
        
    if health:
        lines.append(f"\n🌡️ Group Health: *{health}*")
    
    return "\n".join(lines)

def fmt_recap(data: dict) -> str:
    events = data.get("events", [])
    date_str = data.get("date_str", "")
    summary = data.get("summary", "")
    
    if not events:
        return "📌 *24h Recap*\n\n_No significant events in the last 24 hours._"
        
    lines = [f"📋 *Last 24 Hrs*"]
    if date_str:
        lines.append(date_str)
    lines.append("")
    
    for ev in events:
        actor = ev.get('actor', '')
        action = ev.get('action', '')
        time_str = ev.get('time', '??')
        if " IST" in time_str:
            time_str = time_str.replace(" IST", "")
        lines.append(f"🕒 {time_str} · *{actor}* — {action}")
        
    if summary:
        lines.append("")
        lines.append(f"🔍 *Summary:* {summary}")
        
    return "\n".join(lines)
