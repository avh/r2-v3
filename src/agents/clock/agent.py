"""Clock Tool Agent — answers time/date questions using the local model."""

import zoneinfo
from datetime import datetime
from pathlib import Path

from src.agents.ta_base import run_ta_model

_SYSTEM_PROMPT = (Path(__file__).parent / "system.txt").read_text().strip()


async def handle_question(question: str, ta_session) -> str:
    try:
        tz = zoneinfo.ZoneInfo("America/Los_Angeles")
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
    time_str = now.strftime("%A, %B %-d %Y  %I:%M %p %Z").strip()
    augmented = f"Current date and time: {time_str}\n\nQuestion: {question}"
    return await run_ta_model(augmented, _SYSTEM_PROMPT, ta_session)
