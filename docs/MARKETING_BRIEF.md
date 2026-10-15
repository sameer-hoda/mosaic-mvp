# Mosaic — GTM Briefing & Demo Asset Prompts

> Use this document to brief designers, generate marketing copy, and produce WhatsApp screenshots for landing pages, pitch decks, and social media.

---

## 1. Product Positioning (Elevator Pitch)

**For busy professionals drowning in WhatsApp group messages.**

**Mosaic** is an AI assistant that lives in your WhatsApp and answers questions about your chats — "what's pending on me?", "what's the latest on the widget launch?", "catch me up" — instantly, privately, without leaving WhatsApp.

**Unlike Slack bots or email summaries**, Mosaic works where you already are. It reads your groups, finds what matters, and replies in a private chat. Nobody in your groups knows you're using it.

---

## 2. Target Personas

| Persona | Job | Pain Point | Mosaic's Fix |
|---------|-----|-----------|--------------|
| **The PM / Founder** | Running 20+ group chats, stakeholder updates across WhatsApp | Can't track decisions and commitments across groups. Preps for meetings by scrolling. | "What did Rachit say about the partnership?" — answer in seconds |
| **The Operator** | Manages vendors, logistics, team coordination | Action items slip because they're buried in chat threads | "What's pending on me?" — clean list of only *their* commitments |
| **The Executive** | Too many groups, too little time | Needs a quick pulse without reading every message | "Catch me up" — 24h sitrep across all chats |
| **The Power User** | WhatsApp is their primary work tool | Wants proactive intelligence, not reactive scrolling | Daily summaries: "send me vendor updates every morning at 9" |

---

## 3. Key Messages (For Landing Page / Social)

**Headline options:**
1. "Your WhatsApp, finally under control."
2. "An AI assistant that reads your group chats so you don't have to."
3. "What's pending? What did I miss? What's the latest? Ask Mosaic."

**Sub-headlines:**
- "Runs on your own server. Reads your groups. Replies privately. Nobody knows."
- "One WhatsApp chat. Every answer."
- "Deploy in one click. Free tier works."

**Differentiators vs. alternatives:**
- **vs. Slack AI / Notion AI**: Works on WhatsApp, where your real conversations happen.
- **vs. ChatGPT / Claude**: Has context from your actual chat history. Grounded answers, not hallucinations.
- **vs. Hiring an EA**: Costs $0/month (free Gemini tier). Available 24/7. Never misses a message.

---

## 4. Demo Screenshot Prompts

Use these prompts with DALL-E, Midjourney, Ideogram, or any image generation LLM. All prompts are designed to produce **WhatsApp UI screenshots** showing Mosaic in action.

Each prompt follows a consistent format: `[Scene] + [Visual requirements] + [WhatsApp constraints]`.

---

### Prompt 1 — Mosaic Welcome

```
A WhatsApp chat screen on an iPhone showing a welcome message from an AI assistant. The contact name at the top is "Mosaic" with a small robot avatar. The assistant sent a message bubble saying "🤖 *Mosaic* is ready. I'll help you stay on top of your chats. Ask me things like: what's pending on me, catch me up, or what's the latest on any topic." The message uses WhatsApp asterisk *markdown* for bold text. The chat has a clean, modern dark mode interface. The message bubble is in WhatsApp green. Below the welcome message, the user replied "hey". REALISTIC WhatsApp UI — rounded message bubbles, timestamps on each message, the WhatsApp input bar at the bottom with a text field and send button. Technical constraints: iPhone screen proportions, dark mode theme, WhatsApp authentic UI elements. --style realistic --ar 9:16
```

### Prompt 2 — "What's pending on me?"

```
A WhatsApp chat screen on an iPhone. The contact is "Mosaic" with a robot avatar. The user sent: "what's pending on me". Mosaic replied with a long message showing action items grouped by chat group name. The message text has WhatsApp formatting — *bold* for key terms, bullet points, and group names. The message bubble is longer/taller to show the detailed response. At the bottom right of the screen there's a small methodology footer reading "_Searched 6 groups · 2 relevant · 14 messages used_". The user's next reply is "thanks". REALISTIC WhatsApp UI — dark mode, green bubbles for Mosaic, grey bubbles for user messages, timestamps on each message, standard WhatsApp input bar at the bottom. The chat list is visible in the background (subtle blur). --style realistic --ar 9:16
```

### Prompt 3 — Deep Search ("What's the latest on the widget launch?")

```
A WhatsApp chat screen on an iPhone. Contact is "Mosaic". The user asked: "what's the latest on the widget launch". Mosaic's response is a structured summary with sections: a header saying "*Widget Launch*", then groups listed by name — "Widget - daily tracking to 10Mn", "Young pensioners" — with specific update points under each. Each update has dates in `monospace` formatting like `Aug 05`. The message bubble is tall with multiple lines. At the end, a methodology footer: "_Searched 12 groups · 4 relevant · 86 messages used_". REALISTIC WhatsApp UI — dark mode, iPhone proportions, green assistant bubbles, authentic typography, WhatsApp-like message spacing. Below Mosaic's reply, the user sent a thumbs-up emoji "👍". --style realistic --ar 9:16
```

### Prompt 4 — Multi-turn Conversation

```
A WhatsApp chat screen on an iPhone showing a conversation between a user and "Mosaic" (AI assistant with robot avatar). Top message from user: "catch me up". Mosaic replies with a sitrep showing items grouped by chat — several short bullet points about decisions, blockers, and updates from different groups. Next, user asks: "tell me more about the win page changes". Mosaic replies with a detailed list of changes sourced from specific chat groups, with group names in *bold* and dates in `monospace`. The conversation shows 4 messages total — user, mosaic, user, mosaic — each with appropriate timestamps. REALISTIC WhatsApp UI — dark mode, green bubbles vs grey bubbles, iPhone screen proportions, authentic WhatsApp design language. --style realistic --ar 9:16
```

### Prompt 5 — Web Console / Setup Experience

```
A laptop screen showing the Mosaic setup wizard. Dark theme web page with a card-style layout. The heading says "Mosaic" in a clean sans-serif font with a blue accent color. Below, "Create Console Password — Set a password to secure your Mosaic console. Choose something you'll remember." Two input fields: "Password (min 6 characters)" and "Confirm password", both with dark backgrounds and blue borders on focus. A green "Set Password" button. The page has a subtle dark grid background. Professional, clean, SaaS-style design. Realistic web UI elements — no exaggerated lighting or fantasy elements. --style realistic --ar 16:9
```

### Prompt 6 — Landing Page Hero Image (WhatsApp + AI concept)

```
A minimalist marketing hero image for a product called "Mosaic". An iPhone floating in the center showing a WhatsApp chat screen with an AI assistant response visible. The chat shows "🤖 *Mosaic* is ready..." message. Behind the phone, subtle geometric mosaic patterns in gradient blues and purples, suggesting AI intelligence and data processing. Clean white/light grey background. Professional product photography style. The phone is angled slightly (3D perspective). No text overlays on the image itself — leave space for a headline. Tech product aesthetic, similar to Linear or Stripe landing pages. --style realistic --ar 16:9
```

### Prompt 7 — Social Media Carousel (Slide 1 of 3)

```
A product marketing graphic for social media. Clean dark background with a subtle gradient. At the top, bold white text: "Your WhatsApp, finally under control." Below, a centered iPhone mockup showing a WhatsApp chat with "Mosaic" where the user asked "what's pending on me" and got a structured response. The phone has a subtle drop shadow. At the bottom, a small logo and URL: mosaic.so. Modern SaaS marketing design — minimal, clean, high contrast. Text is crisp and readable. Like a Stripe or Linear product announcement graphic. --style realistic --ar 1:1
```

### Prompt 8 — Comparison Graphic (Before/After)

```
A split-screen comparison graphic. Left side labeled "BEFORE" in small grey text: an iPhone showing WhatsApp with 15+ unread group chats and a frustrated "scrolling through messages" visual with blurred message content. Right side labeled "AFTER" in small green text: an iPhone showing a clean WhatsApp chat with Mosaic, where Mosaic replied "Nothing pending on you right now" above a short structured response. Between the two phones, a right-arrow icon. Clean dark background. Professional marketing style. The message is clear: stop scrolling, start asking. --style realistic --ar 16:9
```

---

## 5. Video Demo Script (30 seconds)

For a product demo video or animated GIF on the landing page:

| Time | Screen | Action |
|------|--------|--------|
| 0-3s | WhatsApp chat list | Show 20+ unread group chats. Text overlay: "50 groups. 500+ messages/day." |
| 3-8s | Open Mosaic chat | User types: "what's pending on me". Smooth type animation. |
| 8-15s | Mosaic responds | Mosaic's message appears — bulleted list of 4-5 action items grouped by chat. Slight message-by-message reveal. |
| 15-22s | User types follow-up | "what's the latest on widget launch". Mosaic replies with detailed, sourced update. |
| 22-27s | Close-up of methodology footer | "_Searched 12 groups · 4 relevant · 86 messages used_". Text overlay: "Grounded. Private. Yours." |
| 27-30s | Logo + CTA | Mosaic logo. "Deploy in one click. mosaic.so". QR code to deploy. |

---

## 6. Distribution Channels

| Channel | Asset | Timeline |
|---------|-------|----------|
| Product Hunt | Landing page + demo video + first 3 prompts as screenshots | Launch day |
| Twitter/X | Carousel prompts (#7) + thread explaining the problem | Launch week |
| LinkedIn | Before/after graphic (#8) + founder story about WhatsApp overload | Launch week |
| WhatsApp communities | Share demo GIF directly in relevant groups | Ongoing |
| YC / startup forums | Technical deep-dive post on self-hosting + WhatsApp integration | Week 2 |
| Hacker News | "Show HN: Mosaic — AI assistant that reads your WhatsApp groups" | Week 2 |

---

## 7. Press / Outreach Angle

**Story angles for tech press:**
- "This founder built an AI assistant that reads WhatsApp groups — and it runs on your own server"
- "Why WhatsApp is the next frontier for AI assistants (and Slack missed it)"
- "Mosaic: The AI tool that tells you what's pending across 50 WhatsApp groups"

**Key stats to include:**
- Average WhatsApp user is in 25+ groups
- 100 billion WhatsApp messages sent daily
- WhatsApp has 2B+ users — 3x Slack's user base
- Mosaic processes messages privately, on-device equivalent (self-hosted)
