import pytest

from demurrage import llm
from demurrage.resolver_schemas import ContractDraft


def _object_nodes(schema: dict) -> list[dict]:
    found: list[dict] = []

    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                found.append(node)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(schema)
    return found


def test_openrouter_strict_schema_contract_draft():
    raw = ContractDraft.model_json_schema(ref_template="#/$defs/{model}")
    strict = llm._openrouter_strict_schema(raw)
    assert "exclusiveMinimum" not in strict
    for obj in _object_nodes(strict):
        assert obj.get("additionalProperties") is False
        assert set(obj["required"]) == set(obj["properties"])
    assert strict["required"] == list(strict["properties"])
    hours = strict["properties"]["laytime_allowance_hours"]
    assert hours["type"] == ["number", "null"]
    assert "minimum" in hours
    evidence = strict["$defs"]["EvidenceQuote"]
    assert set(evidence["properties"]) == {"source_id", "quote"}


def test_generate_uses_cache(tmp_path, monkeypatch):
    calls = []

    def fake_live(model, messages, schema_name, schema):
        calls.append(1)
        return ContractDraft(status="missing").model_dump_json()

    monkeypatch.setenv(llm.CACHE_ENV, "1")
    monkeypatch.setenv(llm.CACHE_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(llm, "_live_generate", fake_live)

    system = "You are a resolver."
    user = "Extract contract from: Laytime 72 hours SHINC."
    out = llm.generate(system, user, ContractDraft)
    again = llm.generate(system, user, ContractDraft)
    assert out.status == "missing"
    assert again.status == "missing"
    assert calls == [1]


def test_generate_read_miss_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(llm.CACHE_ENV, "read")
    monkeypatch.setenv(llm.CACHE_DIR_ENV, str(tmp_path))
    with pytest.raises(llm.JevCacheMissError):
        llm.generate("s", "u", ContractDraft)


def test_generate_surfaces_openrouter_error(monkeypatch):
    class Response:
        is_success = False
        status_code = 400
        text = "invalid_json_schema"

    monkeypatch.setenv(llm.API_KEY_ENV, "test-key")
    monkeypatch.setenv(llm.CACHE_ENV, "off")
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="400"):
        llm.generate("s", "u", ContractDraft)
