"""LLM boundary: one Pydantic schema is both the requested format and the parser."""

from __future__ import annotations

import json
import os

from pydantic import BaseModel

API_KEY_ENV = "LLM_API_KEY"


def is_configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV))


def call_model(system: str, user: str, json_schema: dict) -> str:
    """Send one structured-output request and return the raw JSON text."""
    raise NotImplementedError(f"LLM client not implemented yet (set up {API_KEY_ENV})")


def structured_call[T: BaseModel](system: str, user: str, schema: type[T]) -> T:
    """Ask the model for `schema`-shaped JSON; raises ValidationError if it isn't."""
    raw = call_model(system, user, schema.model_json_schema())
    return schema.model_validate_json(raw)


def schema_text(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), indent=2)
