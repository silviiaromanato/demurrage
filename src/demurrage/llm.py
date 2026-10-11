"""OpenRouter clients: Jev choice questions, and strict JSON schema generation."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

from demurrage.schema import strict_schema

load_dotenv()

API_KEY_ENV = "LLM_API_KEY"
MODEL_ENV = "DEMURRAGE_MODEL"
RESOLVER_MODEL_ENV = "DEMURRAGE_RESOLVER_MODEL"
CACHE_ENV = "DEMURRAGE_LLM_CACHE"
CACHE_DIR_ENV = "DEMURRAGE_LLM_CACHE_DIR"
DEFAULT_MODEL = "typesafe/jev-1.13"
DEFAULT_RESOLVER_MODEL = "openai/gpt-4.1-mini"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_CACHE_DIR = Path(".llm_cache")


class CacheMissError(RuntimeError):
    """No cached response and live calls are disabled."""


def cache_mode() -> Literal["off", "read", "write", "readwrite"]:
    raw = os.environ.get(CACHE_ENV, "").strip().lower()
    if raw in ("", "0", "false", "off", "no"):
        return "off"
    if raw in ("read", "readonly", "read-only"):
        return "read"
    if raw in ("write", "write-only"):
        return "write"
    return "readwrite"


def cache_dir() -> Path:
    return Path(os.environ.get(CACHE_DIR_ENV, DEFAULT_CACHE_DIR))


def is_configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV)) or cache_mode() != "off"


def model_name() -> str:
    return os.environ.get(MODEL_ENV, DEFAULT_MODEL)


def resolver_model_name() -> str:
    return os.environ.get(RESOLVER_MODEL_ENV, DEFAULT_RESOLVER_MODEL)


def _cached(payload: dict[str, Any], field: str, live: Callable[[], Any]) -> Any:
    """Serve `field` from the cache file for `payload`, or call `live` and store it."""
    mode = cache_mode()
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    path = cache_dir() / f"{hashlib.sha256(blob).hexdigest()}.json"
    if mode in ("read", "readwrite") and path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))[field]
    if mode == "read":
        raise CacheMissError(
            f"no cached {payload['kind']} response for {path.name[:12]}… "
            f"(set {CACHE_ENV}=readwrite and run once with {API_KEY_ENV})"
        )
    value = live()
    if mode in ("write", "readwrite"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({field: value}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return value


def _post(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    if not os.environ.get(API_KEY_ENV):
        raise RuntimeError(f"{API_KEY_ENV} is not set; cannot call OpenRouter")
    response = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {os.environ[API_KEY_ENV]}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=timeout,
    )
    if not response.is_success:
        raise RuntimeError(
            f"OpenRouter request failed ({response.status_code}): "
            f"{response.text[:4000]}"
        )
    return response.json()


def decide(state: str | dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
    """One Jev request: state + typed questions → answers keyed by question id."""
    body = {"model": model_name(), "state": state, "questions": questions}
    return _cached(
        {"kind": "jev", **body},
        "answers",
        lambda: _post(DECISIONS_URL, body, timeout=30.0)["answers"],
    )


def choice_question(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def choose(
    state: str | dict[str, Any],
    question_id: str,
    instructions: str,
    criteria: dict[str, str],
) -> dict[str, Any]:
    """Ask one choice question and return its Jev answer."""
    answers = decide(state, {question_id: choice_question(instructions, criteria)})
    answer = answers[question_id]
    if answer.get("choice") not in criteria:
        raise ValueError(
            f"{question_id}: {answer.get('choice')!r} not in {list(criteria)}"
        )
    return answer


def _complete(
    model: str, messages: list[dict[str, str]], name: str, schema: dict[str, Any]
) -> str:
    reply = _post(
        CHAT_URL,
        {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        },
        timeout=120.0,
    )
    content = reply["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("resolver returned non-string message content")
    return content


def generate[T: BaseModel](
    system: str, user: str, schema: type[T], name: str | None = None
) -> T:
    """OpenRouter chat completion with strict JSON schema; result is validated."""
    name = name or schema.__name__
    json_schema = strict_schema(schema.model_json_schema())
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    model = resolver_model_name()
    content = _cached(
        {
            "kind": "generate",
            "model": model,
            "messages": messages,
            "schema_name": name,
            "schema": json_schema,
        },
        "content",
        lambda: _complete(model, messages, name, json_schema),
    )
    return schema.model_validate_json(content)
