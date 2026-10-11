"""Jev (TypeSafe) via OpenRouter: typed choice answers, not generated text."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY_ENV = "LLM_API_KEY"
MODEL_ENV = "DEMURRAGE_MODEL"
CACHE_ENV = "DEMURRAGE_JEV_CACHE"
CACHE_DIR_ENV = "DEMURRAGE_JEV_CACHE_DIR"
DEFAULT_MODEL = "typesafe/jev-1.13"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
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


def _cache_key(state: str | dict[str, Any], questions: dict[str, Any]) -> str:
    payload = {
        "model": model_name(),
        "state": state,
        "questions": questions,
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


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
