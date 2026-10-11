import pytest

from demurrage import llm


def test_decide_readwrite_cache(tmp_path, monkeypatch):
    calls = []

    def fake_live(state, questions):
        calls.append(1)
        return {"q": {"choice": "yes", "confidence": 1.0, "probabilities": {}}}

    monkeypatch.setenv(llm.CACHE_ENV, "1")
    monkeypatch.setenv(llm.CACHE_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(llm, "_live_decide", fake_live)

    q = {"q": llm.choice_question("?", {"yes": "y", "no": "n"})}
    assert llm.decide({"body": "a"}, q)["q"]["choice"] == "yes"
    assert llm.decide({"body": "a"}, q)["q"]["choice"] == "yes"
    assert calls == [1]


def test_decide_readonly_miss_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(llm.CACHE_ENV, "read")
    monkeypatch.setenv(llm.CACHE_DIR_ENV, str(tmp_path))
    with pytest.raises(llm.JevCacheMissError):
        llm.decide({"x": 1}, {"q": llm.choice_question("?", {"yes": "y"})})
