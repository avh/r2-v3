import json
from pathlib import Path


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(*paths: str | Path) -> dict:
    config = {}
    for path in paths:
        p = Path(path)
        if p.exists():
            with open(p) as f:
                config = _deep_merge(config, json.load(f))
    return config
