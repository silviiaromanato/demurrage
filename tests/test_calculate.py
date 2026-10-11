import pytest

from demurrage.calculate import apply_laytime_durations, compute_demurrage
from demurrage.models import CargoPortLaytime, ContractTerms, EvidenceRef, Stoppage

H = 3600


def _contract(**update) -> ContractTerms:
    """DOMAIN.md example: 48 h reversible, USD 20,000/day pro rata, 6 h turn time."""
    base = {
        "status": "resolved",
        "laytime_allowance_hours": 48,
        "reversible": True,
        "turn_time_after_nor_hours": 6,
        "demurrage_rate_per_day": 20000,
        "currency": "USD",
        "evidence": [EvidenceRef(source_id="email_01.eml:abc", quote="recap")],
    }
    return ContractTerms(**(base | update))


def _stoppage(start: str, end: str, fraction: float | None = 1.0) -> Stoppage:
    return Stoppage(
        description="stop",
        cause="vessel",
        start=f"2026-03-01T{start}:00+00:00",
        end=f"2026-03-01T{end}:00+00:00",
        fraction_deducted=fraction,
    )


def _port(*stoppages: Stoppage, **update) -> CargoPortLaytime:
    """NOR 01 Mar 08:00, so laytime runs 14:00 → 02 Mar 20:00 (30 h gross)."""
    base = {
        "visit_id": "pv-1",
        "port": "Antwerp",
        "nor_tendered": "2026-03-01T08:00:00+00:00",
        "final_hoses_disconnected": "2026-03-02T20:00:00+00:00",
        "stoppages": list(stoppages),
        "status": "final",
    }
    return CargoPortLaytime(**(base | update))


def _filled(port: CargoPortLaytime) -> CargoPortLaytime:
    return apply_laytime_durations(_contract(), [port])[0]


def test_domain_example_owes_8333_33():
    load = _port(_stoppage("15:00", "17:00"))
    discharge = _port(
        visit_id="pv-3",
        port="Cotonou",
        nor_tendered="2026-03-10T22:00:00+00:00",
        final_hoses_disconnected="2026-03-12T10:00:00+00:00",
    )
    ports = apply_laytime_durations(_contract(), [load, discharge])
    assert [p.used_duration_seconds / H for p in ports] == [28, 30]
    demurrage = compute_demurrage(_contract(), ports)
    assert demurrage.excess_seconds == 10 * H
    assert demurrage.amount_owed == 8333.33
    assert demurrage.status == "final"


def test_laytime_start_is_nor_plus_turn_time_even_if_given():
    port = _filled(_port(laytime_start="2026-01-01T00:00:00+00:00"))
    assert port.laytime_start == "2026-03-01T14:00:00+00:00"
    assert port.gross_duration_seconds == 30 * H


def test_half_laytime_deducts_half():
    port = _filled(_port(_stoppage("15:00", "19:00", fraction=0.5)))
    assert port.deducted_duration_seconds == 2 * H


def test_counts_in_full_deducts_nothing():
    port = _filled(_port(_stoppage("15:00", "19:00", fraction=0.0)))
    assert port.deducted_duration_seconds == 0
    assert port.used_duration_seconds == 30 * H


def test_stoppage_is_clipped_to_laytime_window():
    port = _filled(_port(_stoppage("10:00", "16:00")))
    assert port.deducted_duration_seconds == 2 * H


def test_overlapping_stoppages_are_not_deducted_twice():
    port = _filled(
        _port(_stoppage("15:00", "19:00"), _stoppage("17:00", "21:00", fraction=0.5))
    )
    assert port.deducted_duration_seconds == 4 * H + 1 * H


@pytest.mark.parametrize(
    "port",
    [
        _port(_stoppage("15:00", "17:00", fraction=None)),
        _port(final_hoses_disconnected=None),
        _port(nor_tendered=None),
    ],
    ids=["no-fraction", "no-hoses-off", "no-nor"],
)
def test_missing_input_leaves_used_and_amount_empty(port):
    filled = _filled(port)
    assert filled.used_duration_seconds is None
    demurrage = compute_demurrage(_contract(), [filled])
    assert demurrage.amount_owed is None
    assert demurrage.status == "incomplete"


def test_provisional_port_makes_demurrage_provisional():
    ports = apply_laytime_durations(_contract(), [_port(status="provisional")])
    assert compute_demurrage(_contract(), ports).status == "provisional"


def test_unresolved_contract_gives_no_amount():
    contract = ContractTerms(status="missing")
    ports = apply_laytime_durations(_contract(), [_port()])
    assert compute_demurrage(contract, ports).amount_owed is None


def test_non_reversible_is_not_supported_yet():
    contract = _contract(reversible=False)
    ports = apply_laytime_durations(contract, [_port()])
    demurrage = compute_demurrage(contract, ports)
    assert demurrage.amount_owed is None
    assert "not supported" in demurrage.calculation_formula
