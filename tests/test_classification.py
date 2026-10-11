import json
from pathlib import Path

import pytest

from demurrage import llm
from demurrage.answer import extractions_for_active_voyage
from demurrage.emails import (
    advance_extraction,
    extract,
    parse_eml_file,
    replay_reviews,
    review_for,
)
from demurrage.models import NOT_OUR_VESSEL, VESSEL_UNCERTAIN, EmailReview

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
    assert (e.vessel_name, e.relevance) == ("KESTREL BAY", "relevant")
    assert e.topics == TOPIC_FLAGS
    assert review_for(e, FLEET) is None


def test_not_about_a_voyage_skips_calls_2_and_3(monkeypatch, parsed):
    e, calls = _run(monkeypatch, parsed, {"voyage": _choice("no")})
    assert calls == ["voyage"]
    assert e.relevance == "not_relevant"
    assert review_for(e, FLEET) is None


def test_uncertain_voyage_raises_review_before_vessel(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("uncertain"),
            "vessel": _choice("unknown"),
            **{topic: _choice("unknown") for topic in TOPIC_FLAGS},
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


def test_legacy_unknown_vessel_raises_review(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("yes"),
            "vessel": _choice("unknown"),
            **{topic: _choice(flag) for topic, flag in TOPIC_FLAGS.items()},
        },
    )
    assert review_for(e, FLEET).code == "VESSEL_UNCERTAIN"


def test_jev_not_our_vessel_skips_review(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("yes"),
            "vessel": _choice(NOT_OUR_VESSEL),
            **{topic: _choice(flag) for topic, flag in TOPIC_FLAGS.items()},
        },
    )
    assert e.voyage_about == "no"
    assert e.relevance == "not_relevant"
    assert review_for(e, FLEET) is None


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
    review = review_for(e, FLEET)
    assert review.code == "TOPIC_contract"
    answered = _answered("TOPIC_contract", "yes", ["yes", "no"])
    e = advance_extraction(e, answered, FLEET, [answered])
    assert e.topics["contract"] == "yes"
    assert review_for(e, FLEET).code == "TOPIC_laytime"


def test_applying_voyage_no_clears_later_reviews(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("uncertain"),
            "vessel": _choice("unknown"),
            **{topic: _choice("unknown") for topic in TOPIC_FLAGS},
        },
    )
    answered = _answered("VOYAGE_UNCERTAIN", "no", ["yes", "no"])
    e = advance_extraction(e, answered, FLEET, [answered])
    assert e.voyage_about == "no"
    assert review_for(e, FLEET) is None


def test_other_fleet_vessel_is_not_relevant(monkeypatch, parsed):
    e, _ = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("yes"),
            "vessel": _choice("KESTREL POINT"),
            **{topic: _choice("no") for topic in TOPIC_FLAGS},
        },
    )
    assert e.relevance == "not_relevant"
    assert extractions_for_active_voyage([e], FLEET) == []


def test_replay_skips_vessel_when_voyage_is_no(monkeypatch, parsed):
    e, _ = _run(monkeypatch, parsed, {"voyage": _choice("no")})
    opts = [*VESSELS, NOT_OUR_VESSEL]
    vessel_answer = _answered("VESSEL_UNCERTAIN", "KESTREL BAY", opts)
    e = replay_reviews(e, [vessel_answer], FLEET)
    assert e.voyage_about == "no"
    assert e.vessel_name is None


def test_jev_failure_yields_pending(monkeypatch, parsed):
    def boom(*_args, **_kwargs):
        raise ValueError("network")

    monkeypatch.setenv(llm.API_KEY_ENV, "test")
    monkeypatch.setattr(llm, "choose", boom)
    e = extract(parsed, FLEET)
    assert e.extraction_status == "pending"
    assert any("Jev failed" in w for w in e.warnings)


def test_vessel_outside_fleet_yields_pending(monkeypatch, parsed):
    e, calls = _run(
        monkeypatch,
        parsed,
        {
            "voyage": _choice("yes"),
            "vessel": _choice("MT OTHER"),
            **{topic: _choice("no") for topic in TOPIC_FLAGS},
        },
    )
    assert calls == ["voyage", "vessel"]
    assert e.extraction_status == "pending"
    assert any("Jev failed" in w for w in e.warnings)
