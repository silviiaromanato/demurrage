import pytest

from demurrage.derive import apply_rule_fractions, finish_contract, placed_time
from demurrage.models import (
    ContractTerms,
    DeductionRule,
    EvidenceRef,
    ResolverQuestion,
    Stoppage,
    WrittenTime,
)

REF = EvidenceRef(source_id="email_01.eml:abc", first_line=1, last_line=1)


@pytest.mark.parametrize(
    ("value", "basis", "offset", "expected"),
    [
        ("2026-02-15T16:20", "local", "+01:00", "2026-02-15T16:20:00+01:00"),
        ("2026-02-15T15:20", "utc", "+01:00", "2026-02-15T16:20:00+01:00"),
        ("2026-02-15T23:30", "utc", "+01:00", "2026-02-16T00:30:00+01:00"),
        ("2026-02-03T12:00", "utc", None, "2026-02-03T12:00:00+00:00"),
        ("2026-02-15T16:20", "local", None, None),
        ("2026-02-15T16:20+01:00", "local", "+01:00", None),
        ("15/1520", "utc", "+01:00", None),
    ],
    ids=[
        "local",
        "utc-to-local",
        "utc-crosses-midnight",
        "utc-at-sea",
        "local-without-offset",
        "model-added-offset",
        "not-iso",
    ],
)
def test_placed_time(value, basis, offset, expected):
    assert placed_time(WrittenTime(value=value, basis=basis), offset) == expected


def _contract(**update) -> ContractTerms:
    base = {
        "laytime_allowance": 3,
        "laytime_allowance_unit": "days",
        "turn_time_after_nor_hours": 6,
        "demurrage_rate_per_day": 25500,
        "currency": "USD",
        "evidence": [REF],
    }
    return ContractTerms(**(base | update))


def test_contract_converts_days_and_is_resolved_with_all_inputs():
    contract = finish_contract(_contract(), [])
    assert contract.laytime_allowance_hours == 72
    assert contract.status == "resolved"


def test_contract_missing_an_input_is_missing():
    assert finish_contract(_contract(currency=None), []).status == "missing"
    assert finish_contract(_contract(laytime_allowance_unit=None), []).status == (
        "missing"
    )


def test_contract_with_a_question_is_conflicted():
    question = ResolverQuestion(prompt="Which rate?", options=["24000", "25500"])
    assert finish_contract(_contract(), [question]).status == "conflicted"


def test_stoppage_fraction_comes_from_its_rule():
    contract = _contract(
        deduction_rules=[
            DeductionRule(kind="weather", fraction_deducted=0.5, evidence=REF)
        ]
    )
    stops = [
        Stoppage(description="rain", cause="weather", deduction_rule="weather"),
        Stoppage(description="berth", cause="berth_waiting"),
        Stoppage(description="?", cause="other", deduction_rule="invented"),
    ]
    apply_rule_fractions(contract, stops)
    assert [s.fraction_deducted for s in stops] == [0.5, 0.0, None]
