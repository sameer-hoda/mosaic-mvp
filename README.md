# Mosaic — WhatsApp Chief of Staff (MVP)

One product, three jobs: **ask** ("what's pending on me?"), **slash commands**
in any chat, and a **pulse** (3 daily briefs + rare real-time pings).
Replies are always drafts you approve with one word.

## Scope deviation from the merge-plan PDF

No separate Mosaic group is created. Group creation risks violating
Meta/WhatsApp guidelines, so the bot talks only in the user's **self-chat**
("Message to self"). Self-chat identity (PN + LID forms) must resolve exactly;
no other chat may ever receive a message.

## Base

Forked from [mosaic-wa](https://github.com/sameer-hoda/mosaic-wa).
Port functions, not folders. See `docs/MVP-SCOPE.md` for the Week 1 / Week 2 plan.
