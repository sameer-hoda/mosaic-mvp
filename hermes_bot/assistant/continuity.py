import json

from google import genai

from hermes_bot import config
from hermes_bot.assistant.session import Session

CONTINUITY_PROMPT = """You are a conversation continuity detector.

Current conversation topic: {topic}
Last messages from user:
{recent_messages}

New message from user: "{new_message}"

Is the new message continuing the same conversation topic?
Respond with JSON only: {{"continues": true/false, "confidence": 0.0-1.0, "new_topic": "brief topic label if new conversation", "reasoning": "one brief sentence"}}

A message continues the conversation if it is on the same subject, asks a follow-up, or naturally extends the current topic.
It does NOT continue if it is a completely different subject or a clear topic shift.
"""


class ContinuityResult:
    def __init__(self, continues: bool, confidence: float, new_topic: str = "", reasoning: str = ""):
        self.continues = continues
        self.confidence = confidence
        self.new_topic = new_topic
        self.reasoning = reasoning


def check_continuity(session: Session, new_message: str) -> ContinuityResult:
    client = genai.Client(api_key=config.GEMINI_API_KEY)

    recent = "\n".join(
        f"  - {msg}" for msg in session.recent_messages[-5:]
    )

    prompt = CONTINUITY_PROMPT.format(
        topic=session.topic,
        recent_messages=recent or "(none)",
        new_message=new_message,
    )

    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL_FAST,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        data = json.loads(response.text)
        return ContinuityResult(
            continues=data.get("continues", True),
            confidence=data.get("confidence", 0.5),
            new_topic=data.get("new_topic", ""),
            reasoning=data.get("reasoning", ""),
        )
    except Exception as e:
        print(f"[continuity] Error: {e}")
        return ContinuityResult(continues=True, confidence=0.5)
