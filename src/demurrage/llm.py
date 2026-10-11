"""OpenRouter clients: Jev choice questions, and strict JSON schema generation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

API_KEY_ENV = "LLM_API_KEY"
MODEL_ENV = "DEMURRAGE_MODEL"
RESOLVER_MODEL_ENV = "DEMURRAGE_RESOLVER_MODEL"
CACHE_ENV = "DEMURRAGE_JEV_CACHE"
CACHE_DIR_ENV = "DEMURRAGE_JEV_CACHE_DIR"
DEFAULT_MODEL = "typesafe/jev-1.13"
DEFAULT_RESOLVER_MODEL = "openai/gpt-4.1-mini"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_CACHE_DIR = Path(".jev_cache")


class JevCacheMissError(RuntimeError):
    """No cached Jev response and live calls are disabled."""


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
    if os.environ.get(API_KEY_ENV):
        return True
    return cache_mode() != "off"


def model_name() -> str:
    return os.environ.get(MODEL_ENV, DEFAULT_MODEL)


def resolver_model_name() -> str:
    return os.environ.get(RESOLVER_MODEL_ENV, DEFAULT_RESOLVER_MODEL)


def _hash_payload(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _cache_key(state: str | dict[str, Any], questions: dict[str, Any]) -> str:
    return _hash_payload(
        {"kind": "jev", "model": model_name(), "state": state, "questions": questions}
    )


def _generative_cache_key(
    model: str, messages: list[dict[str, str]], schema_name: str, schema: dict[str, Any]
) -> str:
    return _hash_payload(
        {
            "kind": "generate",
            "model": model,
            "messages": messages,
            "schema_name": schema_name,
            "schema": schema,
        }
    )


def _cache_path(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def _read_cache(key: str) -> dict[str, Any] | None:
    path = _cache_path(key)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["answers"]


def _write_cache(key: str, answers: dict[str, Any]) -> None:
    root = cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = _cache_path(key)
    path.write_text(
        json.dumps({"answers": answers}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_generative_cache(key: str) -> str | None:
    path = _cache_path(key)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["content"]


def _write_generative_cache(key: str, content: str) -> None:
    root = cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    _cache_path(key).write_text(
        json.dumps({"content": content}, indent=2) + "\n",
        encoding="utf-8",
    )


def _flatten_nullable_anyof(node: dict[str, Any]) -> dict[str, Any]:
    """Turn `anyOf: [<type>, null]` into `type: [<type>, \"null\"]` for strict mode."""
    any_of = node.get("anyOf")
    if not isinstance(any_of, list):
        return node
    null_variants = [v for v in any_of if v.get("type") == "null"]
    other_variants = [v for v in any_of if v.get("type") != "null"]
    if len(null_variants) != 1 or len(other_variants) != 1:
        return node
    merged = {k: v for k, v in node.items() if k != "anyOf"}
    base = dict(other_variants[0])
    inner_type = base.pop("type")
    merged.update(base)
    merged["type"] = [inner_type, "null"] if inner_type != "null" else "null"
    return merged


def _openrouter_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic JSON Schema → OpenRouter/OpenAI strict structured-output subset."""

    def walk(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        out = {k: v for k, v in node.items() if k != "default"}
        if "$defs" in out:
            out["$defs"] = {name: walk(defn) for name, defn in out["$defs"].items()}
        out = _flatten_nullable_anyof(out)
        if "exclusiveMinimum" in out:
            floor = out.pop("exclusiveMinimum")
            out.setdefault("minimum", floor if floor != 0 else 1e-9)
        if "exclusiveMaximum" in out:
            ceiling = out.pop("exclusiveMaximum")
            out.setdefault("maximum", ceiling)
        if out.get("type") == "object" and isinstance(out.get("properties"), dict):
            out["additionalProperties"] = False
            out["properties"] = {
                key: walk(prop) for key, prop in out["properties"].items()
            }
            out["required"] = list(out["properties"])
        if "items" in out:
            out["items"] = walk(out["items"])
        for key in ("allOf", "oneOf", "anyOf"):
            if key in out and isinstance(out[key], list):
                out[key] = [walk(item) for item in out[key]]
        return out

    return walk(copy.deepcopy(schema))


def _live_decide(
    state: str | dict[str, Any], questions: dict[str, Any]
) -> dict[str, Any]:
    if not os.environ.get(API_KEY_ENV):
        raise RuntimeError(
            f"{API_KEY_ENV} is not set; cannot call Jev without a cache hit"
        )
    response = httpx.post(
        DECISIONS_URL,
        headers={
            "Authorization": f"Bearer {os.environ[API_KEY_ENV]}",
            "Content-Type": "application/json",
        },
        json={"model": model_name(), "state": state, "questions": questions},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["answers"]


def decide(state: str | dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
    """One Jev request: state + typed questions → answers keyed by question id."""
    mode = cache_mode()
    key = _cache_key(state, questions)
    if mode in ("read", "readwrite"):
        if cached := _read_cache(key):
            return cached
        if mode == "read":
            raise JevCacheMissError(
                f"no cached Jev response for key {key[:12]}… "
                f"(set {CACHE_ENV}=readwrite and run once with {API_KEY_ENV})"
            )
    answers = _live_decide(state, questions)
    if mode in ("write", "readwrite"):
        _write_cache(key, answers)
    return answers


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


def _live_generate(
    model: str,
    messages: list[dict[str, str]],
    schema_name: str,
    schema: dict[str, Any],
) -> str:
    if not os.environ.get(API_KEY_ENV):
        raise RuntimeError(
            f"{API_KEY_ENV} is not set; cannot call the resolver without a cache hit"
        )
    response = httpx.post(
        CHAT_URL,
        headers={
            "Authorization": f"Bearer {os.environ[API_KEY_ENV]}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        },
        timeout=120.0,
    )
    if not response.is_success:
        raise RuntimeError(
            f"OpenRouter chat completion failed ({response.status_code}): "
            f"{response.text[:4000]}"
        )
    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("resolver returned non-string message content")
    return content


def generate[T: BaseModel](
    system: str,
    user: str,
    schema: type[T],
    name: str | None = None,
) -> T:
    """OpenRouter chat completion with strict JSON schema; result is validated."""
    schema_name = name or schema.__name__
    json_schema = _openrouter_strict_schema(
        schema.model_json_schema(ref_template="#/$defs/{model}")
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    model = resolver_model_name()
    mode = cache_mode()
    key = _generative_cache_key(model, messages, schema_name, json_schema)
    if mode in ("read", "readwrite"):
        if cached := _read_generative_cache(key):
            return schema.model_validate_json(cached)
        if mode == "read":
            raise JevCacheMissError(
                f"no cached resolver response for key {key[:12]}… "
                f"(set {CACHE_ENV}=readwrite and run once with {API_KEY_ENV})"
            )
    content = _live_generate(model, messages, schema_name, json_schema)
    if mode in ("write", "readwrite"):
        _write_generative_cache(key, content)
    return schema.model_validate_json(content)
