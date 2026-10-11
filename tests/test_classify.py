import json
from pathlib import Path

import pytest

from src import llm
from src.classify import extract
from src.fleet import extractions_for_active_voyage, relevance
from src.models import NOT_OUR_VESSEL, VESSEL_UNCERTAIN, EmailReview
from src.parse import parse_eml_file
from src.reviews import advance_extraction, replay_reviews, review_for

FLEET = json.loads(Path("fleet.json").read_text())
VESSELS = [v["name"] for v in FLEET["vessels"]]
TOPIC_FLAGS = {
    "contract": "yes",
    "schedule": "no",
    "laytime": "no",
    "position": "no",
    "other": "no",
}


def _choice(option: str) -> dict:
    return {"type": "choice", "choice": option, "confidence": 0.9, "probabilities": {}}


def _answered(code: str, answer: str, options: list[str]) -> EmailReview:
    return EmailReview(
        code=code,
        prompt="?",
        options=options,
        status="answered",
        answer=answer,
        answered_at="2026-01-01T00:00:00+00:00",
    )


def _fake(replies: dict, calls: list):
    def decide(state, questions):
        out = {}
        for qid in questions:
            calls.append(qid)
            out[qid] = replies[qid]
        return out

    return decide


@pytest.fixture
def parsed():
    return parse_eml_file(Path("emails/email_01.eml"))


def _run(monkeypatch, parsed, replies):
    calls = []
    monkeypatch.setenv(llm.API_KEY_ENV, "test")
    monkeypatch.setattr(llm, "decide", _fake(replies, calls))
    return extract(parsed, FLEET), calls


def _all_topics(flag: str) -> dict:
    return {topic: _choice(flag) for topic in TOPIC_FLAGS}


def test_three_calls_fill_the_extraction(monkeypatch, parsed):
    e, calls = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("yes"),
            "vessel": _choice("KESTREL BAY"),
            **{topic: _choice(flag) for topic, flag in TOPIC_FLAGS.items()},
        },
    )
    assert calls == ["voyage", "vessel", *TOPIC_FLAGS]
    assert e.vessel_name == "KESTREL BAY"
    assert relevance(e, FLEET) == "relevant"
    assert e.topics == TOPIC_FLAGS
    assert review_for(e, FLEET) is None


def test_not_about_a_voyage_skips_calls_2_and_3(monkeypatch, parsed):
    e, calls = _run(monkeypatch, parsed, {"voyage": _choice("no")})
    assert calls == ["voyage"]
    assert relevance(e, FLEET) == "not_relevant"
    assert review_for(e, FLEET) is None


def test_not_our_vessel_skips_topics_and_review(monkeypatch, parsed):
    e, calls = _run(
        monkeypatch,
        parsed,
        {"voyage": _choice("yes"), "vessel": _choice(NOT_OUR_VESSEL)},
    )
    assert calls == ["voyage", "vessel"]
    assert e.voyage_about == "no"
    assert relevance(e, FLEET) == "not_relevant"
    assert review_for(e, FLEET) is None


def test_uncertain_voyage_raises_review_before_vessel(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("uncertain"),
            "vessel": _choice(VESSEL_UNCERTAIN),
            **_all_topics("unknown"),
        },
    )
    review = review_for(e, FLEET)
    assert review.code == "VOYAGE_UNCERTAIN"
    assert review.options == ["yes", "no"]


def test_uncertain_vessel_raises_review(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("yes"),
            "vessel": _choice(VESSEL_UNCERTAIN),
            **{topic: _choice(flag) for topic, flag in TOPIC_FLAGS.items()},
        },
    )
    review = review_for(e, FLEET)
    assert review.code == "VESSEL_UNCERTAIN"
    assert review.options == [*VESSELS, NOT_OUR_VESSEL]


def test_unknown_topic_raises_review_after_vessel(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("yes"),
            "vessel": _choice("KESTREL BAY"),
            "contract": _choice("unknown"),
            "schedule": _choice("no"),
            "laytime": _choice("unknown"),
            "position": _choice("no"),
            "other": _choice("no"),
        },
    )
    assert review_for(e, FLEET).code == "TOPIC_contract"
    answered = _answered("TOPIC_contract", "yes", ["yes", "no"])
    e = advance_extraction(e, answered, FLEET, [answered])
    assert e.topics["contract"] == "yes"
    assert e.review.code == "TOPIC_laytime"


def test_applying_voyage_no_clears_later_reviews(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("uncertain"),
            "vessel": _choice(VESSEL_UNCERTAIN),
            **_all_topics("unknown"),
        },
    )
    answered = _answered("VOYAGE_UNCERTAIN", "no", ["yes", "no"])
    e = advance_extraction(e, answered, FLEET, [answered])
    assert e.voyage_about == "no"
    assert e.review is None


def test_answering_not_our_vessel_marks_email_not_relevant(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("yes"),
            "vessel": _choice(VESSEL_UNCERTAIN),
            **_all_topics("no"),
        },
    )
    answered = _answered("VESSEL_UNCERTAIN", NOT_OUR_VESSEL, [*VESSELS, NOT_OUR_VESSEL])
    e = advance_extraction(e, answered, FLEET, [answered])
    assert (e.voyage_about, e.vessel_name) == ("no", None)
    assert relevance(e, FLEET) == "not_relevant"


def test_other_fleet_vessel_is_not_relevant(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("yes"),
            "vessel": _choice("KESTREL POINT"),
            **_all_topics("no"),
        },
    )
    assert relevance(e, FLEET) == "not_relevant"
    assert extractions_for_active_voyage([e], FLEET) == []


def test_replay_applies_earlier_answers_in_question_order(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("uncertain"),
            "vessel": _choice(VESSEL_UNCERTAIN),
            **_all_topics("no"),
        },
    )
    priors = [
        _answered("VESSEL_UNCERTAIN", "KESTREL BAY", [*VESSELS, NOT_OUR_VESSEL]),
        _answered("VOYAGE_UNCERTAIN", "yes", ["yes", "no"]),
    ]
    e = replay_reviews(e, priors)
    assert (e.voyage_about, e.vessel_name) == ("yes", "KESTREL BAY")
    assert review_for(e, FLEET) is None


def test_replay_skips_vessel_when_voyage_is_no(monkeypatch, parsed):
    e, _ = _run(monkeypatch, parsed, {"voyage": _choice("no")})
    opts = [*VESSELS, NOT_OUR_VESSEL]
    e = replay_reviews(e, [_answered("VESSEL_UNCERTAIN", "KESTREL BAY", opts)])
    assert e.voyage_about == "no"
    assert e.vessel_name is None


def test_jev_failure_yields_pending(monkeypatch, parsed):
    def boom(*_args, **_kwargs):
        raise RuntimeError("OpenRouter request failed (500)")

    monkeypatch.setenv(llm.API_KEY_ENV, "test")
    monkeypatch.setattr(llm, "choose", boom)
    e = extract(parsed, FLEET)
    assert e.extraction_status == "pending"
    assert relevance(e, FLEET) == "uncertain"
    assert any("Jev failed" in w for w in e.warnings)


def test_vessel_outside_fleet_yields_pending(monkeypatch, parsed):
    e, calls = _run(
        monkeypatch,
        parsed,
        {"voyage": _choice("yes"), "vessel": _choice("MT OTHER"), **_all_topics("no")},
    )
    assert calls == ["voyage", "vessel"]
    assert e.extraction_status == "pending"
    assert any("Jev failed" in w for w in e.warnings)


def test_no_llm_configured_yields_pending(monkeypatch, parsed):
    monkeypatch.delenv(llm.API_KEY_ENV, raising=False)
    monkeypatch.setenv(llm.CACHE_ENV, "off")
    e = extract(parsed, FLEET)
    assert e.extraction_status == "pending"
    assert review_for(e, FLEET) is None


def test_position_topic_makes_no_generative_call(monkeypatch):
    parsed = parse_eml_file(Path("emails/email_08.eml"))

    def generate(*args, **kwargs):
        raise AssertionError("classification must not call generate")

    monkeypatch.setattr(llm, "generate", generate)
    extraction, _calls = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("yes"),
            "vessel": _choice("KESTREL BAY"),
            **_all_topics("no"),
            "position": _choice("yes"),
        },
    )
    assert extraction.extraction_status == "complete"
    assert extraction.topics["position"] == "yes"
