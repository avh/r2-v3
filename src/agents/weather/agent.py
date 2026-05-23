"""Weather Tool Agent — uses function calling so the model extracts parameters."""

import json
import urllib.parse
import urllib.request
from pathlib import Path

from src.agents.ta_base import run_ta_with_tools

_SYSTEM_PROMPT = (Path(__file__).parent / "system.txt").read_text().strip()

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Fetch current conditions and a 3-day forecast for a location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City name or place (e.g. 'San Francisco', 'Paris, France'). Omit for the user's local weather.",
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["fahrenheit", "celsius"],
                        "description": "Temperature unit (default: fahrenheit).",
                    },
                },
                "required": [],
            },
        },
    }
]


def get_weather(location: str = "", unit: str = "fahrenheit") -> str:
    encoded = urllib.parse.quote(location)
    url = f"https://wttr.in/{encoded}?format=j1"
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
        data = json.loads(resp.read())

    current = data["current_condition"][0]
    area = data["nearest_area"][0]
    city = area["areaName"][0]["value"]
    country = area["country"][0]["value"]

    if unit == "celsius":
        temp = f"{current['temp_C']}°C (feels like {current['FeelsLikeC']}°C)"
        hi_key, lo_key = "maxtempC", "mintempC"
        deg = "°C"
    else:
        temp = f"{current['temp_F']}°F (feels like {current['FeelsLikeF']}°F)"
        hi_key, lo_key = "maxtempF", "mintempF"
        deg = "°F"

    desc = current["weatherDesc"][0]["value"]
    humidity = current["humidity"]
    wind_mph = current["windspeedMiles"]

    lines = [
        f"Current weather in {city}, {country}:",
        f"  {desc}, {temp}",
        f"  Humidity: {humidity}%, Wind: {wind_mph} mph",
        "",
        "3-day forecast:",
    ]
    for day in data.get("weather", []):
        day_desc = day["hourly"][4]["weatherDesc"][0]["value"]
        lines.append(f"  {day['date']}: {day_desc}, High {day[hi_key]}{deg} / Low {day[lo_key]}{deg}")

    return "\n".join(lines)


async def handle_question(question: str, ta_session) -> str:
    ta_session.log("question", question)
    return await run_ta_with_tools(
        question,
        _SYSTEM_PROMPT,
        _TOOLS,
        {"get_weather": get_weather},
        ta_session,
    )
