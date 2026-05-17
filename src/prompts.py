"""Shared system-prompt builder — all agent prompts start with the top-level system.txt."""

from pathlib import Path

ROOT = Path(__file__).parent.parent


def build_system_prompt(*extra_paths: Path) -> str:
    """Return a system prompt starting with ROOT/system.txt, followed by any extra files."""
    parts = []
    for path in [ROOT / "system.txt", *extra_paths]:
        if path.exists():
            parts.append(path.read_text().strip())
    return "\n\n".join(parts)
