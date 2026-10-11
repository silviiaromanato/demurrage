import pytest
from pydantic import ValidationError

from demurrage.answer import build_voyage_answer
from demurrage.models import (
    CargoPortLaytime,
    ContractTerms,
    DemurrageAnswer,
    EmailExtraction,
    EmailReview,
    EvidenceRef,
    ResolvedVoyageCore,
)
from demurrage.resolver import itinerary_skeleton
from demurrage.resolver_facts import ExtractedFact
from demurrage.validate import validate_answer, validate_resolved

SID = "email_01.eml:abc"
QUOTE = "72 hours reversible, demurrage USD 25,500 pdpr"
VOYAGE = {
    "voyage_no": "KB-2603",
    "itinerary": [
        {"seq": 1, "port": "Antwerp", "function": "load"},
        {"seq": 2, "port": "Gibraltar", "function": "bunkering"},
        {"seq": 3, "port": "Cotonou", "function": "discharge"},
    ],
}
FLEET = {"vessels": [{"name": "KESTREL BAY", "imo": "1", "current_voyage": VOYAGE}]}


def _extraction() -> EmailExtraction:
    return EmailExtraction(
        source_id=SID,
        filename="email_01.eml",
        content_hash="abc",
        message_id=None,
        subject="CP",
        sent_at=None,
        extraction_status="complete",
        voyage_about="yes",
        vessel_name="KESTREL BAY",
        relevance="relevant",
        topics={
            "contract": "yes",
            "schedule": "no",
            "laytime": "no",
            "position": "no",
            "other": "no",
        },
    )


def _core() -> ResolvedVoyageCore:
    skeleton = itinerary_skeleton(VOYAGE)
    ev = [EvidenceRef(source_id=SID, quote=QUOTE)]
    contract = ContractTerms(
        status="resolved",
        laytime_allowance_hours=72,
        reversible=True,
        turn_time_after_nor_hours=6,
        demurrage_rate_per_day=25500,
        currency="USD",
        evidence=ev,
    )
    windows = {
        "pv-1": ("2026-03-01T06:00:00+00:00", "2026-03-02T20:00:00+00:00"),
        "pv-3": ("2026-03-20T00:00:00+00:00", "2026-03-23T23:30:00+00:00"),
    }
    laytime = [
        lt.model_copy(
            update={
                "laytime_start": windows[lt.visit_id][0],
                "final_hoses_disconnected": windows[lt.visit_id][1],
                "status": "final",
            }
        )
        for lt in skeleton.laytime_by_cargo_port
    ]
    return skeleton.model_copy(
        update={"contract": contract, "laytime_by_cargo_port": laytime}
    )


def test_end_to_end_fixture_numbers():
    extractions = [_extraction()]
    core = _core()
    assert validate_resolved(core, VOYAGE, extractions) == []
    answer = build_voyage_answer(FLEET, extractions, core)
    used = [lt.used_duration_seconds / 3600 for lt in answer.laytime_by_cargo_port]
    assert used == [38.0, 95.5]
    assert answer.demurrage.excess_seconds / 3600 == 61.5
    assert answer.demurrage.amount_owed == 65343.75
    assert answer.voyage.status == "final"
    assert validate_answer(answer, extractions) == []


def test_resolver_must_not_fill_durations_or_invent_quotes():
    core = _core()
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
    problems = validate_resolved(bad, VOYAGE, [_extraction()])
    assert any("must not fill" in p for p in problems)
    assert any("unknown missing.eml:0" in p for p in problems)


def test_tampered_answer_fails_recompute():
    extractions = [_extraction()]
    answer = build_voyage_answer(FLEET, extractions, _core())
    d = answer.demurrage.model_dump() | {"daily_rate": 30000, "amount_owed": 76875.0}
    tampered = answer.model_copy(update={"demurrage": DemurrageAnswer(**d)})
    assert "demurrage does not recompute" in " ".join(
        validate_answer(tampered, extractions)
    )


@pytest.mark.parametrize(
    "build",
    [
        lambda: CargoPortLaytime(
            visit_id="pv-1",
            port="A",
            gross_duration_seconds=10,
            deducted_duration_seconds=2,
            used_duration_seconds=9,
        ),
        lambda: DemurrageAnswer(
            total_used_seconds=100, allowed_seconds=50, excess_seconds=40
        ),
        lambda: EmailReview(
            code="X", prompt="?", options=["a"], status="answered", answer="b"
        ),
        lambda: ExtractedFact(
            fact_id="other#1",
            fact_type="t",
            value=1,
            status="final",
            quote="q",
            source_id=SID,
        ),
        lambda: ContractTerms(status="resolved"),
    ],
)
def test_models_reject_inconsistent_data(build):
    with pytest.raises(ValidationError):
        build()
