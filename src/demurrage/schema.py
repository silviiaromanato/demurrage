"""Pydantic JSON Schema → the strict subset OpenRouter structured outputs accept."""

from __future__ import annotations

import copy
from typing import Any


def _flatten_nullable(node: dict[str, Any]) -> dict[str, Any]:
    """Turn `anyOf: [<type>, null]` into `type: [<type>, "null"]`."""
    any_of = node.get("anyOf")
    if not isinstance(any_of, list):
        return node
    nulls = [v for v in any_of if v.get("type") == "null"]
    others = [v for v in any_of if v.get("type") != "null"]
    if len(nulls) != 1 or len(others) != 1:
        return node
    merged = {k: v for k, v in node.items() if k != "anyOf"}
    base = dict(others[0])
    inner_type = base.pop("type")
    merged.update(base)
    merged["type"] = [inner_type, "null"]
    return merged


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Every object closed and fully required; no defaults or exclusive bounds."""

    def walk(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        out = {k: v for k, v in node.items() if k != "default"}
        if "$defs" in out:
            out["$defs"] = {name: walk(defn) for name, defn in out["$defs"].items()}
        out = _flatten_nullable(out)
        if "exclusiveMinimum" in out:
            floor = out.pop("exclusiveMinimum")
            out.setdefault("minimum", floor if floor != 0 else 1e-9)
        if "exclusiveMaximum" in out:
            out.setdefault("maximum", out.pop("exclusiveMaximum"))
        if out.get("type") == "object" and isinstance(out.get("properties"), dict):
            out["additionalProperties"] = False
            out["properties"] = {k: walk(p) for k, p in out["properties"].items()}
            out["required"] = list(out["properties"])
        if "items" in out:
            out["items"] = walk(out["items"])
        for key in ("allOf", "oneOf", "anyOf"):
            if isinstance(out.get(key), list):
                out[key] = [walk(item) for item in out[key]]
        return out

    return walk(copy.deepcopy(schema))
