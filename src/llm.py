from __future__ import annotations

import json
import re
from typing import Any

import httpx

from src import USER_AGENT
from src.settings import ai_base_url, env

DEFAULT_PLANNER_MODEL = "deepseek-v4-flash"
DEFAULT_SYNTHESIZER_MODEL = "deepseek-v4-flash"


def resolve_model(role: str) -> str:
    """Use Secrets as-is. Do not auto-pick a different vendor model."""
    configured = env("AI_MODEL_PLANNER") if role == "planner" else env("AI_MODEL_SYNTHESIZER")
    if configured:
        return configured
    if role == "synthesizer":
        return env("AI_MODEL_PLANNER") or DEFAULT_SYNTHESIZER_MODEL
    return DEFAULT_PLANNER_MODEL


class LLMError(RuntimeError):
    pass


def _client() -> httpx.Client:
    key = env("AI_API_KEY")
    if not key:
        raise LLMError("AI_API_KEY is not set")
    return httpx.Client(
        base_url=ai_base_url(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        timeout=httpx.Timeout(90.0, connect=15.0),
        follow_redirects=True,
    )


def chat(messages: list[dict[str, str]], *, model: str, max_tokens: int) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    with _client() as client:
        response = client.post("/chat/completions", json=payload)
        if response.status_code >= 400:
            retry = dict(payload)
            retry.pop("max_tokens", None)
            retry["max_completion_tokens"] = max_tokens
            response = client.post("/chat/completions", json=retry)
        if response.status_code >= 400:
            raise LLMError(f"LLM HTTP {response.status_code}: {response.text[:400]}")
        body = response.json()
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected LLM payload: {body!r}"[:400]) from exc
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        content = "".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM returned empty content")
    return content.strip()


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(text[start : end + 1])
        if isinstance(data, dict):
            return data
    raise LLMError("Could not parse JSON from model output")
