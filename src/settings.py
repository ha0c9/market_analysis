from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from src import CONFIG_DIR, DEFAULT_BASE_URL


def load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return (value or default).strip()


def ai_base_url() -> str:
    """Use AI_BASE_URL exactly as stored in Secrets (trim trailing slash only)."""
    return env("AI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(data) + "\n", encoding="utf-8")
