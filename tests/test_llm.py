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


class _Reply:
    def __init__(self, payload: dict):
        self._payload = payload
        self.is_success = True
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._payload


def test_live_call_records_cost_and_cache_hit_does_not(cache, tmp_path, monkeypatch):
    usage = tmp_path / "usage.jsonl"
    monkeypatch.setenv(llm.CACHE_ENV, "1")
    monkeypatch.setenv(llm.USAGE_ENV, str(usage))
    monkeypatch.setenv(llm.API_KEY_ENV, "test-key")
    monkeypatch.setenv(llm.MODEL_ENV, llm.DEFAULT_MODEL)
    llm.reset_usage()
    reply = _Reply(
        {
            "id": "gen-1",
            "answers": {"q": {"choice": "yes", "confidence": 1.0, "probabilities": {}}},
            "usage": {
                "cost": 0.000042,
                "prompt_tokens": 100,
                "completion_tokens": 0,
                "total_tokens": 100,
            },
        }
    )
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: reply)
    question = {"q": llm.choice_question("?", {"yes": "y", "no": "n"})}
    llm.decide({"body": "a"}, question)
    llm.decide({"body": "a"}, question)

    text = llm.cost_summary()
    assert text is not None
    assert "1 live call, $0.000042 this command" in text
    assert "typesafe/jev-1.13 (jev): 1 call, $0.000042, 100 in / 0 out" in text
    assert "1 cache hit, not charged" in text
    assert f"across 1 call in {usage}" in text
    assert len(usage.read_text().splitlines()) == 1


def test_missing_usage_cost_is_reported(tmp_path, monkeypatch):
    usage = tmp_path / "usage.jsonl"
    monkeypatch.setenv(llm.CACHE_ENV, "off")
    monkeypatch.setenv(llm.USAGE_ENV, str(usage))
    monkeypatch.setenv(llm.API_KEY_ENV, "test-key")
    llm.reset_usage()
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *args, **kwargs: _Reply({"id": "gen-2", "choices": []}),
    )
    llm._post(llm.CHAT_URL, {"model": "openai/gpt-4.1-mini"}, 1.0)
    text = llm.cost_summary()
    assert text is not None
    assert "1 live call, $0.00 this command" in text
    assert "1 call had no usage.cost" in text
    assert "1 call without a cost" in text


def test_message_reasoning_prefers_plaintext():
    assert (
        llm._message_reasoning(
            {
                "reasoning": "check the recap first",
                "reasoning_details": [
                    {"type": "reasoning.summary", "summary": "recap then addendum"}
                ],
            }
        )
        == "check the recap first\n\nrecap then addendum"
    )


def test_message_reasoning_notes_encrypted_only():
    assert llm._message_reasoning(
        {"reasoning_details": [{"type": "reasoning.encrypted", "data": "abc"}]}
    ) == "(no printable reasoning; details types: reasoning.encrypted)"


def test_complete_prints_reasoning_when_asked(monkeypatch, capsys):
    captured: list[dict] = []

    def fake_post(url, body, timeout):
        captured.append(body)
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"status":"missing"}',
                        "reasoning": "NOR plus turn time starts laytime",
                    }
                }
            ]
        }

    monkeypatch.setenv(llm.API_KEY_ENV, "test-key")
    monkeypatch.setenv(llm.REASONING_ENV, "1")
    monkeypatch.setattr(llm, "_post", fake_post)
    content = llm._complete("openai/gpt-6-sol", [], "ContractDraft", {})
    assert content == '{"status":"missing"}'
    assert captured[0]["reasoning"] == {"enabled": True, "exclude": False}
    printed = capsys.readouterr().out
    assert "--- reasoning: ContractDraft ---" in printed
    assert "NOR plus turn time starts laytime" in printed
    assert "--- end reasoning: ContractDraft ---" in printed


def test_complete_omits_reasoning_request_by_default(monkeypatch, capsys):
    captured: list[dict] = []

    def fake_post(url, body, timeout):
        captured.append(body)
        return {
            "choices": [
                {
                    "message": {
                        "content": "{}",
                        "reasoning": "should not print",
                    }
                }
            ]
        }

    monkeypatch.setenv(llm.API_KEY_ENV, "test-key")
    monkeypatch.delenv(llm.REASONING_ENV, raising=False)
    monkeypatch.setattr(llm, "_post", fake_post)
    llm._complete("openai/gpt-4.1-mini", [], "LaytimeDraft", {})
    assert "reasoning" not in captured[0]
    assert capsys.readouterr().out == ""
