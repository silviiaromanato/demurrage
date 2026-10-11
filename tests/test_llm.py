import pytest

from demurrage import llm
from demurrage.models import ContractTerms


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv(llm.CACHE_DIR_ENV, str(tmp_path))
    return tmp_path


def _choice_reply(*_args, **_kwargs):
    return {"answers": {"q": {"choice": "yes", "confidence": 1.0, "probabilities": {}}}}


def test_decide_caches_in_readwrite_mode(cache, monkeypatch):
    calls = []
    monkeypatch.setenv(llm.CACHE_ENV, "1")
    monkeypatch.setattr(
        llm, "_post", lambda *a, **k: calls.append(1) or _choice_reply()
    )
    q = {"q": llm.choice_question("?", {"yes": "y", "no": "n"})}
    assert llm.decide({"body": "a"}, q)["q"]["choice"] == "yes"
    assert llm.decide({"body": "a"}, q)["q"]["choice"] == "yes"
    assert calls == [1]
    assert len(list(cache.iterdir())) == 1


def test_decide_read_only_miss_raises(cache, monkeypatch):
    monkeypatch.setenv(llm.CACHE_ENV, "read")
    with pytest.raises(llm.CacheMissError):
        llm.decide({"x": 1}, {"q": llm.choice_question("?", {"yes": "y"})})


def test_choose_rejects_choice_outside_criteria(cache, monkeypatch):
    monkeypatch.setenv(llm.CACHE_ENV, "off")
    monkeypatch.setattr(llm, "_post", _choice_reply)
    with pytest.raises(ValueError, match="not in"):
        llm.choose({}, "q", "?", {"no": "n"})


def test_generate_caches_and_validates(cache, monkeypatch):
    calls = []

    def fake_complete(model, messages, name, schema):
        calls.append(name)
        return ContractTerms(status="missing").model_dump_json()

    monkeypatch.setenv(llm.CACHE_ENV, "1")
    monkeypatch.setattr(llm, "_complete", fake_complete)
    for _ in range(2):
        out = llm.generate("You are a resolver.", "Laytime 72 hours.", ContractTerms)
        assert out.status == "missing"
    assert calls == ["ContractTerms"]


def test_generate_read_only_miss_raises(cache, monkeypatch):
    monkeypatch.setenv(llm.CACHE_ENV, "read")
    with pytest.raises(llm.CacheMissError):
        llm.generate("s", "u", ContractTerms)


def test_openrouter_error_body_is_surfaced(monkeypatch):
    class Response:
        is_success = False
        status_code = 400
        text = "invalid_json_schema"

    monkeypatch.setenv(llm.API_KEY_ENV, "test-key")
    monkeypatch.setenv(llm.CACHE_ENV, "off")
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="400.*invalid_json_schema"):
        llm.generate("s", "u", ContractTerms)


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv(llm.API_KEY_ENV, raising=False)
    monkeypatch.setenv(llm.CACHE_ENV, "off")
    with pytest.raises(RuntimeError, match=llm.API_KEY_ENV):
        llm.generate("s", "u", ContractTerms)
