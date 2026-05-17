"""Clock Tool Agent — returns current date and time."""

from datetime import datetime
import zoneinfo


async def handle_question(question: str, ta_session) -> str:
    try:
        tz = zoneinfo.ZoneInfo("America/Los_Angeles")
        now = datetime.now(tz)
        return now.strftime("%A, %B %-d %Y  %I:%M %p %Z")
    except Exception as e:
        return datetime.now().strftime("%A, %B %-d %Y  %I:%M %p") + " (local)"
