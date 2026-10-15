#!/usr/bin/env python3
"""
Invoked by the Go bridge when a MeChat message arrives from the owner.

Usage: python3 mechat_handler.py <chat_jid> <sender_jid> <message_text>
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermes_bot.assistant.transcript import Transcript
from hermes_bot.assistant.pipeline import run_pipeline
from hermes_bot.sender import enqueue_to_mechat
from hermes_bot.db import is_message_from_owner


def _log(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[mosaic {ts}] {msg}", flush=True)


def main():
    if len(sys.argv) < 4:
        _log("ERROR: missing args")
        sys.exit(1)

    chat_jid = sys.argv[1]
    sender_jid = sys.argv[2]
    message_text = " ".join(sys.argv[3:])

    _log(f"INVOKED chat={chat_jid} sender={sender_jid} msg=\"{message_text[:60]}\"")

    if not message_text.strip():
        _log("SKIP: empty message")
        return

    if message_text.strip().startswith("/"):
        _log("SKIP: slash command (bridge handles these)")
        return

    if not is_message_from_owner(chat_jid, sender_jid):
        _log("SKIP: not owner")
        return

    enqueue_to_mechat("⚡ *On it* — figuring out what you need…")

    transcript = Transcript()
    _log(f"Transcript loaded: {len(transcript.entries)} entries")

    reply = run_pipeline(message_text, transcript.get_formatted(), progress=_progress)
    _log(f"Pipeline reply: \"{reply[:80]}...\"")

    transcript.add("user", message_text)
    transcript.add("assistant", reply)
    transcript.save()

    _send_reply(reply)
    _log("DONE")


def _send_reply(text: str):
    _log(f"Enqueueing {len(text)} chars to outbox")
    enqueue_to_mechat(text)


def _progress(msg: str):
    _log(f"Progress: {msg}")
    enqueue_to_mechat(msg)


if __name__ == "__main__":
    main()
