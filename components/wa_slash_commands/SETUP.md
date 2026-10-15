# 🚀 Setup Guide — wa-slash-commands

## Step 1 — Install dependencies

```bash
pip3 install -r requirements.txt
```

## Step 2 — Run the setup wizard

```bash
python3 setup.py
```

The wizard will walk you through:
- Entering your Gemini API key
- Setting your WhatsApp phone number (owner lock)
- Pointing to your bridge's SQLite databases
- Running a quick local test to confirm everything works

That's it. You're ready to go.

---

## Running manually (after setup)

```bash
python3 wacmd.py <chat_jid> <sender_jid> "<command>"
```

Example:
```bash
python3 wacmd.py 12345678@g.us 919876543210@s.whatsapp.net "/help"
```

---

## Available Commands

| Command | What it does |
|---|---|
| `/sotu` | 30-day state of the union |
| `/pending` | Open threads & next steps with owners |
| `/stats` | Team personas & activity share |
| `/recap` | 24h signal-only timeline |
| `/eli5 <topic>` | Explain anything using chat as context |
| `/help` | List all commands |
