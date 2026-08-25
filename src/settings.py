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
    """Normalize OpenAI-compatible Chat Completions base URL.

    SSSAiCode Anthropic 格式是 ``.../api``，OpenAI 格式是 ``.../api/v1``。
    未配模型名时客户端会请求 ``{base}/chat/completions``，少 ``/v1`` 会 404。
    """
    url = env("AI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    for suffix in ("/chat/completions", "/messages", "/responses"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    return url


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(data) + "\n", encoding="utf-8")
