"""Small live-LLM check: at most 1 real Gemini call, opt-in only.

Runs ONLY when MOSAIC_RUN_LIVE=1 and a real GEMINI_API_KEY is present.
Otherwise skipped — the suite stays offline. This is the single place a
live call may happen (budget for the whole suite: max 5).
"""

import os

import pytest

from hermes_bot import gemini as gm

LIVE = os.getenv("MOSAIC_RUN_LIVE") == "1" and bool(os.getenv("GEMINI_API_KEY"))

pytestmark = pytest.mark.skipif(not LIVE, reason="live LLM check is opt-in")


def test_live_extract_one_call():
    got = gm.extract_items(
        "Dev: Sameer, please share the Q3 numbers by EOD?\nAsha: will do",
        chat="Work Group",
    )
    assert isinstance(got.get("items"), list)
