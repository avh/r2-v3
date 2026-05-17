"""Help Tool Agent — lists available agents and their descriptions."""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent.parent
AGENTS_DIR = ROOT / "src" / "agents"

# Agents not exposed to PAs
_HIDDEN = {"personal"}


def _get_agent_descriptions() -> dict[str, str]:
    descriptions = {}
    for agent_dir in sorted(AGENTS_DIR.iterdir()):
        if not agent_dir.is_dir() or agent_dir.name.startswith("_"):
            continue
        if agent_dir.name in _HIDDEN:
            continue
        system_txt = agent_dir / "system.txt"
        if system_txt.exists():
            first_line = system_txt.read_text().strip().splitlines()[0]
            descriptions[agent_dir.name] = first_line
    return descriptions


async def handle_question(question: str, ta_session) -> str:
    descriptions = _get_agent_descriptions()
    if not descriptions:
        return "No Tool Agents are currently available."
    lines = [f"- **{name}**: {desc}" for name, desc in descriptions.items()]
    return "Available agents:\n" + "\n".join(lines)
