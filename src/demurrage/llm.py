"""OpenRouter clients: Jev choice questions, and strict JSON schema generation."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
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
USAGE_ENV = "DEMURRAGE_USAGE_PATH"
REASONING_ENV = "DEMURRAGE_PRINT_REASONING"
DEFAULT_MODEL = "typesafe/jev-1.13"
DEFAULT_RESOLVER_MODEL = "openai/gpt-4.1-mini"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_CACHE_DIR = Path(".llm_cache")
DEFAULT_USAGE_PATH = Path("usage.jsonl")

# Live calls made in this process. Cache hits are counted separately: a replay
# does not charge again.
_ledger: list[dict[str, Any]] = []
_cache_hits = 0


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


def usage_path() -> Path:
    return Path(os.environ.get(USAGE_ENV, DEFAULT_USAGE_PATH))


def reset_usage() -> None:
    """Clear this process's ledger. The usage file is left as it is."""
    global _cache_hits
    _ledger.clear()
    _cache_hits = 0


def is_configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV)) or cache_mode() != "off"


def model_name() -> str:
    return os.environ.get(MODEL_ENV, DEFAULT_MODEL)


def resolver_model_name() -> str:
    return os.environ.get(RESOLVER_MODEL_ENV, DEFAULT_RESOLVER_MODEL)


def print_reasoning() -> bool:
    raw = os.environ.get(REASONING_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _message_reasoning(message: dict[str, Any]) -> str | None:
    """Plaintext thinking from an OpenRouter chat message, if any."""
    chunks: list[str] = []
    for key in ("reasoning", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            chunks.append(value.strip())
    types: list[str] = []
    details = message.get("reasoning_details")
    if isinstance(details, list):
        for item in details:
            if not isinstance(item, dict):
                continue
            types.append(str(item.get("type") or "unknown"))
            for key in ("text", "summary"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    chunks.append(value.strip())
    if chunks:
        return "\n\n".join(chunks)
    if types:
        return f"(no printable reasoning; details types: {', '.join(types)})"
    return None


def _emit_reasoning(name: str, reasoning: str | None) -> None:
    print(f"--- reasoning: {name} ---")
    print(reasoning or "(none returned; some OpenAI models withhold the raw chain)")
    print(f"--- end reasoning: {name} ---")


def _cached(payload: dict[str, Any], field: str, live: Callable[[], Any]) -> Any:
    """Serve `field` from the cache file for `payload`, or call `live` and store it."""
    mode = cache_mode()
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    path = cache_dir() / f"{hashlib.sha256(blob).hexdigest()}.json"
    if mode in ("read", "readwrite") and path.is_file():
        _note_cache_hit()
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
    reply = response.json()
    _record(url, body, reply)
    return reply


def _kind_for(url: str) -> str:
    if url == DECISIONS_URL:
        return "jev"
    if url == CHAT_URL:
        return "generate"
    return "other"


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _usage_fields(reply: dict[str, Any]) -> dict[str, Any]:
    usage = reply.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return {
        "cost_usd": _optional_float(usage.get("cost")),
        "prompt_tokens": _optional_int(usage.get("prompt_tokens")),
        "completion_tokens": _optional_int(usage.get("completion_tokens")),
        "total_tokens": _optional_int(usage.get("total_tokens")),
    }


def _note_cache_hit() -> None:
    global _cache_hits
    _cache_hits += 1


def _record(url: str, body: dict[str, Any], reply: dict[str, Any]) -> None:
    """Append one live call to the process ledger and to the usage file."""
    row = {
        "at": datetime.now(UTC).isoformat(),
        "kind": _kind_for(url),
        "model": str(body.get("model", "")),
        "id": reply.get("id") if isinstance(reply.get("id"), str) else None,
        **_usage_fields(reply),
    }
    _ledger.append(row)
    path = usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _usd(amount: float) -> str:
    text = f"{amount:.8f}".rstrip("0").rstrip(".")
    if "." not in text:
        text += ".00"
    return f"${text}"


def _count(n: int, singular: str) -> str:
    return f"{n} {singular if n == 1 else singular + 's'}"


def _file_totals() -> tuple[int, float, int]:
    path = usage_path()
    if not path.is_file():
        return 0, 0.0, 0
    calls = unknown = 0
    spent = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        calls += 1
        cost = row.get("cost_usd")
        if cost is None:
            unknown += 1
        else:
            spent += float(cost)
    return calls, spent, unknown


def cost_summary() -> str | None:
    """What this process spent, plus the total recorded in the usage file.

    Empty when nothing was called and nothing was served from the cache.
    """
    if not _ledger and _cache_hits == 0:
        return None
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    total = 0.0
    unknown = 0
    for row in _ledger:
        groups.setdefault((row["kind"], row["model"]), []).append(row)
        if row["cost_usd"] is None:
            unknown += 1
        else:
            total += row["cost_usd"]
    lines = [f"cost: {_count(len(_ledger), 'live call')}, {_usd(total)} this command"]
    for (kind, model), rows in groups.items():
        cost = sum(row["cost_usd"] or 0.0 for row in rows)
        prompt = sum(row["prompt_tokens"] or 0 for row in rows)
        completion = sum(row["completion_tokens"] or 0 for row in rows)
        lines.append(
            f"  {model} ({kind}): {_count(len(rows), 'call')}, {_usd(cost)}, "
            f"{prompt} in / {completion} out"
        )
    if unknown:
        lines.append(f"  {_count(unknown, 'call')} had no usage.cost")
    if _cache_hits:
        lines.append(f"  {_count(_cache_hits, 'cache hit')}, not charged")
    calls, spent, file_unknown = _file_totals()
    if calls:
        recorded = (
            f"cost: {_usd(spent)} across {_count(calls, 'call')} in {usage_path()}"
        )
        if file_unknown:
            recorded += f", {_count(file_unknown, 'call')} without a cost"
        lines.append(recorded)
    return "\n".join(lines)


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
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        },
    }
    if print_reasoning():
        body["reasoning"] = {"enabled": True, "exclude": False}
    reply = _post(CHAT_URL, body, timeout=120.0)
    message = reply["choices"][0]["message"]
    content = message["content"]
    if not isinstance(content, str):
        raise ValueError("resolver returned non-string message content")
    if print_reasoning():
        _emit_reasoning(name, _message_reasoning(message))
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
            "print_reasoning": print_reasoning(),
        },
        "content",
        lambda: _complete(model, messages, name, json_schema),
    )
    return schema.model_validate_json(content)
