"""Help Tool Agent — uses a model to identify relevant agents for a question."""

from pathlib import Path

from src.agents.ta_base import run_ta_model

ROOT = Path(__file__).parent.parent.parent.parent
AGENTS_DIR = ROOT / "src" / "agents"
_SYSTEM_PROMPT = (Path(__file__).parent / "system.txt").read_text().strip()

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
    agents_list = "\n".join(f"- {name}: {desc}" for name, desc in descriptions.items())
    augmented = f"Available agents:\n{agents_list}\n\nQuestion: {question}"
    return await run_ta_model(augmented, _SYSTEM_PROMPT, ta_session)
