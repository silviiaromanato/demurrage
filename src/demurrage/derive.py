"""Values Python derives from what the resolver read. The model does no arithmetic."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel

from demurrage.models import (
    CargoPortLaytime,
    ContractTerms,
    LatestPosition,
    PortVisit,
    ResolverQuestion,
    Stoppage,
    WrittenTime,
)

TIME_FIELDS: dict[type, tuple[str, ...]] = {
    PortVisit: ("arrival", "departure"),
    CargoPortLaytime: ("nor_tendered", "final_hoses_disconnected"),
    Stoppage: ("start", "end"),
    LatestPosition: ("reported_at",),
}
HOURS_PER_UNIT = {"hours": 1, "days": 24}


def _offset(text: str | None) -> timezone | None:
    """'+01:00' -> timezone(+1h); None when missing or malformed."""
    if not text or len(text) != 6 or text[0] not in "+-" or text[3] != ":":
        return None
    try:
        hours, minutes = int(text[1:3]), int(text[4:6])
    except ValueError:
        return None
    sign = 1 if text[0] == "+" else -1
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def placed_time(written: WrittenTime, utc_offset: str | None) -> str | None:
    """ISO 8601 in port local time; None if unparseable or local with no offset."""
    try:
        naive = datetime.fromisoformat(written.value)
    except ValueError:
        return None
    if naive.tzinfo is not None:
        return None
    local = _offset(utc_offset)
    if written.basis == "utc":
        return naive.replace(tzinfo=UTC).astimezone(local or UTC).isoformat()
    return naive.replace(tzinfo=local).isoformat() if local else None


def timed_rows(core: Any) -> Iterator[tuple[str, BaseModel, str | None]]:
    """(where, row, visit_id) for every row that carries times; visit_id None at sea."""
    for visit in core.port_visits:
        yield visit.visit_id, visit, visit.visit_id
    for lt in core.laytime_by_cargo_port:
        yield f"laytime {lt.visit_id}", lt, lt.visit_id
        for stop in lt.stoppages:
            yield f"stoppage {lt.visit_id}", stop, lt.visit_id
    if core.latest_position is not None:
        yield "latest_position", core.latest_position, None


def place_times(core: Any, offsets: dict[str, str | None]) -> None:
    """Fill each ISO time from its `*_written` twin and the port's UTC offset."""
    for _, row, visit_id in timed_rows(core):
        for name in TIME_FIELDS[type(row)]:
            written = getattr(row, f"{name}_written")
            offset = offsets.get(visit_id) if visit_id else None
            setattr(row, name, placed_time(written, offset) if written else None)


def finish_contract(
    contract: ContractTerms, questions: list[ResolverQuestion]
) -> ContractTerms:
    """Allowance in hours, and a status that follows from the evidence."""
    hours = None
    if contract.laytime_allowance and contract.laytime_allowance_unit:
        hours = (
            contract.laytime_allowance * HOURS_PER_UNIT[contract.laytime_allowance_unit]
        )
    inputs = (
        hours,
        contract.turn_time_after_nor_hours,
        contract.demurrage_rate_per_day,
        contract.currency,
    )
    if questions:
        status = "conflicted"
    elif None not in inputs and contract.evidence:
        status = "resolved"
    else:
        status = "missing"
    return ContractTerms.model_validate(
        contract.model_dump() | {"laytime_allowance_hours": hours, "status": status}
    )


def apply_rule_fractions(contract: ContractTerms, stoppages: list[Stoppage]) -> None:
    """A stoppage takes its rule's fraction; no rule counts in full; unknown is None."""
    fractions = {r.kind: r.fraction_deducted for r in contract.deduction_rules}
    for stop in stoppages:
        if stop.deduction_rule is None:
            stop.fraction_deducted = 0.0
        else:
            stop.fraction_deducted = fractions.get(stop.deduction_rule)
