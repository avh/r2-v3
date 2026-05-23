"""Aviation Tool Agent — METAR/TAF lookups and general aviation knowledge."""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from src.agents.ta_base import run_ta_with_tools

_SYSTEM_PROMPT = (Path(__file__).parent / "system.txt").read_text().strip()

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_metar",
            "description": (
                "Fetch the latest METAR (current weather observation) for an airport. "
                "Use the ICAO identifier (e.g. KSFO, KLAX, EGLL, RJTT). "
                "For US airports prefix the FAA code with K (LAX → KSFO)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "station": {
                        "type": "string",
                        "description": "ICAO station identifier, e.g. 'KSFO' or 'EGLL'.",
                    }
                },
                "required": ["station"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_taf",
            "description": (
                "Fetch the Terminal Aerodrome Forecast (TAF) for an airport. "
                "Use the ICAO identifier."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "station": {
                        "type": "string",
                        "description": "ICAO station identifier, e.g. 'KSFO' or 'EGLL'.",
                    }
                },
                "required": ["station"],
            },
        },
    },
]

_BASE = "https://aviationweather.gov/api/data"


def _fetch(url: str) -> list:
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
        return json.loads(resp.read())


def _fmt_time(unix_ts: int | None) -> str:
    if not unix_ts:
        return "unknown"
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def get_metar(station: str) -> str:
    station = station.upper().strip()
    url = f"{_BASE}/metar?ids={urllib.parse.quote(station)}&format=json"
    data = _fetch(url)
    if not data:
        return f"No METAR found for {station}. Verify the ICAO identifier."

    obs = data[0]
    lines = [f"METAR — {obs.get('name', station)} ({station})"]
    lines.append(f"  Observed: {_fmt_time(obs.get('obsTime'))}")
    lines.append(f"  Raw: {obs.get('rawOb', 'N/A')}")
    lines.append(f"  Flight category: {obs.get('fltCat', 'N/A')}")

    temp, dewp = obs.get("temp"), obs.get("dewp")
    if temp is not None:
        lines.append(f"  Temp / Dewpoint: {temp}°C / {dewp}°C")

    wdir, wspd, wgst = obs.get("wdir"), obs.get("wspd"), obs.get("wgst")
    if wspd is not None:
        wind = f"  Wind: {wdir}° at {wspd} kt"
        if wgst:
            wind += f" gusting {wgst} kt"
        lines.append(wind)

    vis = obs.get("visib")
    if vis is not None:
        lines.append(f"  Visibility: {vis} SM")

    clouds = obs.get("clouds")
    if clouds:
        cld_str = ", ".join(
            f"{c['cover']} {c['base']}ft" for c in clouds if c.get("cover") not in ("CAVOK", "CLR", "SKC")
        ) or "Clear"
        lines.append(f"  Clouds: {cld_str}")

    altim = obs.get("altim")
    if altim:
        inhg = altim * 0.02953
        lines.append(f"  Altimeter: {inhg:.2f} inHg ({altim:.0f} hPa)")

    return "\n".join(lines)


def get_taf(station: str) -> str:
    station = station.upper().strip()
    url = f"{_BASE}/taf?ids={urllib.parse.quote(station)}&format=json"
    data = _fetch(url)
    if not data:
        return f"No TAF found for {station}. Verify the ICAO identifier."

    taf = data[0]
    lines = [f"TAF — {taf.get('name', station)} ({station})"]
    lines.append(f"  Issued: {taf.get('issueTime', 'unknown')}")
    lines.append(f"  Raw: {taf.get('rawTAF', 'N/A')}")
    return "\n".join(lines)


async def handle_question(question: str, ta_session) -> str:
    ta_session.log("question", question)
    return await run_ta_with_tools(
        question,
        _SYSTEM_PROMPT,
        _TOOLS,
        {"get_metar": get_metar, "get_taf": get_taf},
        ta_session,
    )
