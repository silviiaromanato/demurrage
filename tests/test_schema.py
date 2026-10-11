from demurrage.models import ContractTerms, ResolvedVoyageCore
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
    hours = strict["properties"]["laytime_allowance_hours"]
    assert hours["type"] == ["number", "null"]
    assert hours["minimum"] > 0
    assert "exclusiveMinimum" not in str(strict)
    assert "default" not in str(strict)


def test_input_schema_is_not_mutated():
    raw = ContractTerms.model_json_schema()
    before = str(raw)
    strict_schema(raw)
    assert str(raw) == before
