from demurrage.models import (
    ContractDraft,
    ContractTerms,
    LaytimeDraft,
    ResolvedVoyageCore,
)
from demurrage.schema import strict_schema


def _objects(node) -> list[dict]:
    found = []
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            found.append(node)
        for value in node.values():
            found += _objects(value)
    elif isinstance(node, list):
        for item in node:
            found += _objects(item)
    return found


def test_every_object_is_closed_and_fully_required():
    strict = strict_schema(ResolvedVoyageCore.model_json_schema())
    objects = _objects(strict)
    assert len(objects) > 5
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])


def test_nullable_numbers_become_type_lists_without_exclusive_bounds():
    strict = strict_schema(ContractTerms.model_json_schema())
    hours = strict["properties"]["laytime_allowance"]
    assert hours["type"] == ["number", "null"]
    assert hours["minimum"] > 0
    assert "exclusiveMinimum" not in str(strict)
    assert "default" not in str(strict)


def test_nullable_object_ref_stays_any_of():
    strict = strict_schema(LaytimeDraft.model_json_schema())
    position = strict["properties"]["latest_position"]
    assert position["anyOf"] == [
        {"$ref": "#/$defs/LatestPosition"},
        {"type": "null"},
    ]


def test_ref_has_no_sibling_keywords():
    for draft in (ContractDraft, LaytimeDraft):
        refs = _refs(strict_schema(draft.model_json_schema()))
        assert refs
        assert all(set(node) == {"$ref"} for node in refs)


def _refs(node) -> list[dict]:
    if isinstance(node, dict):
        found = [node] if "$ref" in node else []
        return found + [r for v in node.values() for r in _refs(v)]
    if isinstance(node, list):
        return [r for item in node for r in _refs(item)]
    return []


def test_input_schema_is_not_mutated():
    raw = ContractTerms.model_json_schema()
    before = str(raw)
    strict_schema(raw)
    assert str(raw) == before
