from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlparse

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


def log(message: str) -> None:
    print(message, flush=True)


def public_url_parts(url: str) -> str:
    parsed = urlparse(url)
    return f"scheme={parsed.scheme or '-'} host={parsed.hostname or '-'} path={parsed.path or '/'}"


def model_debug(model: str) -> str:
    lower = model.lower()
    flags = [token for token in ("deepseek", "flash", "vision", "grok", "gpt", "claude") if token in lower]
    return f"len={len(model)} flags={','.join(flags) or 'none'} spaced={str(' ' in model).lower()}"


class LLMError(RuntimeError):
    pass


def _client(timeout: float) -> httpx.Client:
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
        timeout=httpx.Timeout(timeout, connect=15.0),
        follow_redirects=True,
    )


def _brief_body(text: str, limit: int = 280) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit]


def _content_from_message(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        content = "".join(parts)
    if isinstance(content, str) and content.strip():
        return content.strip()
    for key in ("reasoning_content", "refusal"):
        extra = message.get(key)
        if isinstance(extra, str) and extra.strip():
            log(f"llm using message.{key} because content was empty")
            return extra.strip()
    return ""


def probe_llm() -> None:
    base = ai_base_url()
    log(f"llm probe {public_url_parts(base)}")
    planner = resolve_model("planner")
    synthesizer = resolve_model("synthesizer")
    log(f"llm planner {model_debug(planner)}")
    log(f"llm synthesizer {model_debug(synthesizer)}")
    try:
        with _client(30.0) as client:
            started = time.monotonic()
            request = client.build_request("GET", "/models")
            log(f"llm GET {public_url_parts(str(request.url))}")
            response = client.send(request)
            elapsed = int((time.monotonic() - started) * 1000)
            log(f"llm GET /models status={response.status_code} elapsed_ms={elapsed} bytes={len(response.content)}")
            if response.status_code >= 400:
                log(f"llm GET /models body={_brief_body(response.text)}")
                return
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
            log(f"llm models_listed={len(ids)}")
            log(f"llm planner_listed={str(planner in ids).lower()} synthesizer_listed={str(synthesizer in ids).lower()}")
    except Exception as exc:
        log(f"llm GET /models failed: {type(exc).__name__}: {exc}")


def chat(messages: list[dict[str, str]], *, model: str, max_tokens: int, timeout: float = 90.0) -> str:
    attempts: list[dict[str, Any]] = [
        {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens},
        {"model": model, "messages": messages, "max_completion_tokens": max_tokens},
    ]
    last_error = "LLM request failed"
    with _client(timeout) as client:
        for index, payload in enumerate(attempts, start=1):
            request = client.build_request("POST", "/chat/completions", json=payload)
            keys = ",".join(sorted(k for k in payload if k != "messages"))
            log(
                f"llm POST attempt={index} {public_url_parts(str(request.url))} "
                f"{model_debug(model)} payload={keys}"
            )
            started = time.monotonic()
            try:
                response = client.send(request)
            except httpx.TimeoutException as exc:
                last_error = f"LLM timeout after {timeout:.0f}s: {exc}"
                log(f"llm timeout attempt={index} after_ms={int((time.monotonic() - started) * 1000)}")
                continue
            elapsed = int((time.monotonic() - started) * 1000)
            log(
                f"llm status={response.status_code} elapsed_ms={elapsed} "
                f"bytes={len(response.content)} body={_brief_body(response.text)!r}"
            )
            if response.status_code >= 400:
                last_error = f"LLM HTTP {response.status_code}: {response.text[:400]}"
                continue
            try:
                body = response.json()
            except json.JSONDecodeError as exc:
                last_error = f"LLM returned non-JSON: {exc}"
                continue
            try:
                choice = body["choices"][0]
                message = choice["message"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"Unexpected LLM payload: {body!r}"[:400]) from exc
            usage = body.get("usage") or {}
            log(
                f"llm ok finish={choice.get('finish_reason')} "
                f"message_keys={','.join(sorted(str(k) for k in message))} "
                f"usage={usage}"
            )
            content = _content_from_message(message if isinstance(message, dict) else {})
            if not content:
                raise LLMError("LLM returned empty content")
            return content
    raise LLMError(last_error)


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
