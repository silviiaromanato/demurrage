from demurrage.answer import derive_relevance, extractions_for_active_voyage
from demurrage.calculate import apply_laytime_durations, compute_demurrage
from demurrage.models import (
    CargoPortLaytime,
    ContractTerms,
    EmailExtraction,
    EvidenceRef,
    StoppageDeduction,
)
from demurrage.resolver import resolve

VOYAGE = {
    "voyage_no": "KB-2603",
    "itinerary": [
        {"seq": 1, "port": "Antwerp", "function": "load"},
        {"seq": 2, "port": "Gibraltar", "function": "bunkering"},
        {"seq": 3, "port": "Cotonou", "function": "discharge"},
    ],
}
FLEET = {
    "vessels": [
        {"name": "KESTREL BAY", "imo": "1", "current_voyage": VOYAGE},
        {"name": "KESTREL POINT", "imo": "2"},
    ]
}


def _contract() -> ContractTerms:
    return ContractTerms(
        status="resolved",
        laytime_allowance_hours=48,
        reversible=True,
        turn_time_after_nor_hours=6,
        demurrage_rate_per_day=20000,
        currency="USD",
        evidence=[EvidenceRef(source_id="email_01.eml:abc", quote="recap")],
    )


def _extraction(**update) -> EmailExtraction:
    base = {
        "source_id": "email_01.eml:abc",
        "filename": "email_01.eml",
        "content_hash": "abc",
        "message_id": None,
        "subject": "CP",
        "sent_at": None,
        "extraction_status": "complete",
        "voyage_about": "yes",
        "vessel_name": "KESTREL BAY",
        "relevance": "uncertain",
        "topics": {
            "contract": "yes",
            "schedule": "no",
            "laytime": "no",
            "position": "no",
            "other": "no",
        },
    }
    return EmailExtraction(**(base | update))


def test_resolve_returns_empty_cargo_rows():
    core = resolve(VOYAGE, [])
    assert [v.visit_id for v in core.port_visits] == ["pv-1", "pv-2", "pv-3"]
    assert [lt.visit_id for lt in core.laytime_by_cargo_port] == ["pv-1", "pv-3"]
    for lt in core.laytime_by_cargo_port:
        assert lt.nor_tendered is None
        assert lt.gross_duration_seconds is None
        assert lt.stoppages == []


def test_durations_from_nor_and_stoppage():
    row = CargoPortLaytime(
        visit_id="pv-1",
        port="Antwerp",
        nor_tendered="2026-03-01T08:00:00+00:00",
        final_hoses_disconnected="2026-03-02T20:00:00+00:00",
        stoppages=[
            StoppageDeduction(description="Pump failure", duration_seconds=2 * 3600)
        ],
        status="final",
    )
    filled = apply_laytime_durations(_contract(), [row])[0]
    assert filled.laytime_start == "2026-03-01T14:00:00+00:00"
    assert filled.gross_duration_seconds == 30 * 3600
    assert filled.deducted_duration_seconds == 2 * 3600
    assert filled.used_duration_seconds == 28 * 3600


def test_missing_end_leaves_durations_empty():
    row = CargoPortLaytime(
        visit_id="pv-1",
        port="Antwerp",
        nor_tendered="2026-03-01T08:00:00+00:00",
    )
    filled = apply_laytime_durations(_contract(), [row])[0]
    assert filled.laytime_start == "2026-03-01T14:00:00+00:00"
    assert filled.used_duration_seconds is None
    assert compute_demurrage(_contract(), [filled]).amount_owed is None


def test_active_voyage_keeps_only_relevant_emails():
    ours = _extraction()
    other = _extraction(
        source_id="email_04.eml:x",
        filename="email_04.eml",
        vessel_name="KESTREL POINT",
    )
    pending = _extraction(extraction_status="pending", relevance="uncertain")
    kept = extractions_for_active_voyage([ours, other, pending], FLEET)
    assert [e.source_id for e in kept] == ["email_01.eml:abc"]
    assert kept[0].relevance == "relevant"
    assert derive_relevance(pending, FLEET) == "uncertain"
    assert derive_relevance(other, FLEET) == "not_relevant"
