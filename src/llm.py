from __future__ import annotations

import json
import re
from typing import Any

import httpx

from src import USER_AGENT
from src.settings import ai_base_url, env

PLANNER_HINTS = ("haiku", "mini", "flash", "lite", "small")
SYNTH_HINTS = ("sonnet", "gpt-4.1", "gpt-5.4", "gpt-5.5", "gpt-4o")
AVOID_HINTS = ("opus", "codex", "o1", "o3", "reasoner", "deep-research")


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


def list_model_ids() -> list[str]:
    with _client() as client:
        response = client.get("/models")
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else payload
    ids: list[str] = []
    if isinstance(data, list):
        for row in data:
            if isinstance(row, str):
                ids.append(row)
            elif isinstance(row, dict):
                model_id = row.get("id") or row.get("name")
                if model_id:
                    ids.append(str(model_id))
    return ids


def _score_model(model_id: str, hints: tuple[str, ...]) -> int:
    lower = model_id.lower()
    if any(bad in lower for bad in AVOID_HINTS):
        return -100
    score = 0
    for hint in hints:
        if hint in lower:
            score += 10
    return score


def pick_model(role: str, configured: str, available: list[str] | None = None) -> str:
    if configured:
        return configured
    hints = PLANNER_HINTS if role == "planner" else SYNTH_HINTS
    ids = available if available is not None else []
    if not ids:
        try:
            ids = list_model_ids()
        except Exception:
            ids = []
    ranked = sorted(ids, key=lambda mid: _score_model(mid, hints), reverse=True)
    if ranked and _score_model(ranked[0], hints) > 0:
        return ranked[0]
    if ranked:
        return ranked[0]
    return "gpt-5.4-mini" if role == "planner" else "gpt-5.4"


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
