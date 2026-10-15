# Precision spot-check — extracted items → morning brief

Date: 2026-10-15. Sample: 20 random items from the e2e dry-run store
(100 most-recent group chats, 49,560 messages, 110 open items; 30 live
Gemini extractions + rules fallback for the rest). Each item was judged
against its real thread in the read-only snapshot.

Junk = no reasonable owner-actionable next step: banter, broadcast
announcements, FYI forwards, or somebody else's question not directed
at the owner.

## Verdicts

| # | item (truncated) | verdict | reason |
|---|---|---|---|
| 0 | Verify overlay protection across screens | keep | real fraud-thread follow-up, grounded |
| 1 | Book Goa trip return flights | keep | real family-trip topic (note: partly already booked — stale risk) |
| 2 | Setup slack thread for CRED access flow | keep | direct work ask with owner tag |
| 3 | Team BCC®️ - 2 | JUNK | group name as item; announcement thread, no ask |
| 4 | Cakezone coupon issue | keep | live incident thread |
| 5 | DPSPL side closure status | keep | real settlement follow-up |
| 6 | you should be able to access meta ads man | JUNK | FYI to someone else; malformed (`- ` prefix) |
| 7 | Hey Bengaluru runners invite | JUNK | promo-invite spam, no ask |
| 8 | Alternate beneficiary account (payout reversals) | keep | real blocker |
| 9 | Sitero saddle availability | keep | real ask + commitment to update |
| 10 | It's apparently a video phone… | JUNK | pure banter thread |
| 11 | Horrible stock imagery feedback | keep | grounded ask from Abhisek Dey |
| 12 | Hot wax brand recommendation | JUNK | another member's group question, not to owner; stale (Sep 9) |
| 13 | Close bbps/ccbp reward terms | keep | real work discussion |
| 14 | Partner trophy-room feedback escalation | keep | owner-escalation thread |
| 15 | Settlement ownership/roles | keep | real accountability thread |
| 16 | Visa CD-CVM ETAs | keep | real tracking item |
| 17 | Swimathon hydration stations | JUNK | community logistics, not to owner |
| 18 | Add eng folks to core-team group | keep | real team-lead request |
| 19 | Ranking feedback form | JUNK | broadcast announcement, nothing to do |

## Result

13/20 keep, **7/20 junk = 35% junk rate ≈ 1.75 junk items per 5-item
brief. Target (at most 1 junk per brief) is MISSED.**

Junk clusters: community/hobby groups (cycling, running, swimming —
items 7, 10, 12, 17), broadcast announcements restated as asks
(3, 19), and FYI updates to other people (6).

## Follow-ups (not done here)

- Log the extraction source (live vs rules) per item so the next audit
  can attribute junk to a path; this run shows both paths contribute
  (the same digest item surfaced with and without the live key).
- Gate community/broadcast chats harder: require a direct owner signal
  (`q_to_me`, owner mention, or own message) before a thread can yield
  an item, instead of the current nudge/ask-keyword gate.
- Treat "announcement + link/form" shapes as FYI-only, and drop items
  whose `what` is a group name or starts with `- `/quote fragments.
