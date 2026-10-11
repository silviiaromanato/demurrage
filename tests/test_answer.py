import pytest

from demurrage.answer import build_voyage_answer, render_brief
from demurrage.models import ContractTerms, DemurrageAnswer, EvidenceRef
from demurrage.resolver import resolve
from demurrage.reviews import review_for
from demurrage.validate import validate_answer, validate_brief, validate_resolved

SID = "email_01.eml:abc"


@pytest.fixture
def core(voyage):
    """Resolver output for the fixture voyage: 72 h reversible, USD 25,500/day."""
    skeleton = resolve(voyage, [])
    contract = ContractTerms(
        status="resolved",
        laytime_allowance_hours=72,
        reversible=True,
        turn_time_after_nor_hours=6,
        demurrage_rate_per_day=25500,
        currency="USD",
        evidence=[EvidenceRef(source_id=SID, quote="72 hours reversible")],
    )
    events = {
        "pv-1": ("2026-03-01T00:00:00+00:00", "2026-03-02T20:00:00+00:00"),
        "pv-3": ("2026-03-19T18:00:00+00:00", "2026-03-23T23:30:00+00:00"),
    }
    laytime = [
        lt.model_copy(
            update={
                "nor_tendered": events[lt.visit_id][0],
                "final_hoses_disconnected": events[lt.visit_id][1],
                "status": "final",
            }
        )
        for lt in skeleton.laytime_by_cargo_port
    ]
    return skeleton.model_copy(
        update={"contract": contract, "laytime_by_cargo_port": laytime}
    )


def test_resolve_returns_itinerary_skeleton(voyage):
    core = resolve(voyage, [])
    assert [v.visit_id for v in core.port_visits] == ["pv-1", "pv-2", "pv-3"]
    assert [v.laytime_applies for v in core.port_visits] == [True, False, True]
    assert [lt.visit_id for lt in core.laytime_by_cargo_port] == ["pv-1", "pv-3"]
    assert core.contract.status == "missing"


def test_end_to_end_fixture_numbers(core, voyage, fleet, make_extraction):
    extractions = [make_extraction()]
    assert validate_resolved(core, voyage, extractions) == []
    answer = build_voyage_answer(fleet, extractions, core)
    used = [lt.used_duration_seconds / 3600 for lt in answer.laytime_by_cargo_port]
    assert used == [38.0, 95.5]
    assert answer.demurrage.excess_seconds / 3600 == 61.5
    assert answer.demurrage.amount_owed == 65343.75
    assert answer.voyage.status == "final"
    assert answer.emails[0].relevance == "relevant"
    assert validate_answer(answer, extractions) == []
    assert validate_brief(render_brief(answer), answer) == []


def test_open_review_blocks_final_status(core, fleet, make_extraction):
    pending = make_extraction(voyage_about="uncertain")
    pending = pending.model_copy(update={"review": review_for(pending, fleet)})
    answer = build_voyage_answer(fleet, [pending], core)
    assert answer.voyage.status == "incomplete"
    assert answer.review_items[0].code == "VOYAGE_UNCERTAIN"
    assert validate_answer(answer, [pending]) == []


def test_resolver_must_not_fill_durations_or_cite_unknown_emails(
    core, voyage, make_extraction
):
    bad_lt = core.laytime_by_cargo_port[0].model_copy(
        update={"gross_duration_seconds": 1}
    )
    bad_contract = core.contract.model_copy(
        update={"evidence": [EvidenceRef(source_id="missing.eml:0", quote="invented")]}
    )
    bad = core.model_copy(
        update={
            "contract": bad_contract,
            "laytime_by_cargo_port": [bad_lt, core.laytime_by_cargo_port[1]],
        }
    )
    problems = validate_resolved(bad, voyage, [make_extraction()])
    assert any("must not fill" in p for p in problems)
    assert any("unknown missing.eml:0" in p for p in problems)


def test_tampered_answer_fails_recompute(core, fleet, make_extraction):
    extractions = [make_extraction()]
    answer = build_voyage_answer(fleet, extractions, core)
    d = answer.demurrage.model_dump() | {"daily_rate": 30000, "amount_owed": 76875.0}
    tampered = answer.model_copy(update={"demurrage": DemurrageAnswer(**d)})
    assert "demurrage does not recompute" in " ".join(
        validate_answer(tampered, extractions)
    )
