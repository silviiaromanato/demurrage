"""Python-only arithmetic: laytime durations and demurrage."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.models import (
    CargoPortLaytime,
    ContractTerms,
    DemurrageAnswer,
    Stoppage,
)

SECONDS_PER_DAY = 86400


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def deducted_seconds(
    window_start: datetime, window_end: datetime, stoppages: list[Stoppage]
) -> int | None:
    """Stoppage time × fraction inside the laytime window; overlaps count once.

    Where stoppages overlap, the largest fraction applies to the shared time.
    None if any stoppage lacks a start, an end, or a fraction.
    """
    spans = []
    for s in stoppages:
        start, end = _parse(s.start), _parse(s.end)
        if start is None or end is None or s.fraction_deducted is None:
            return None
        start, end = max(start, window_start), min(end, window_end)
        if start < end:
            spans.append((start, end, s.fraction_deducted))
    cuts = sorted({t for start, end, _ in spans for t in (start, end)})
    total = 0.0
    for a, b in zip(cuts, cuts[1:], strict=False):
        covering = [f for start, end, f in spans if start <= a and b <= end]
        if covering:
            total += (b - a).total_seconds() * max(covering)
    return round(total)


def apply_laytime_durations(
    contract: ContractTerms, ports: list[CargoPortLaytime]
) -> list[CargoPortLaytime]:
    """Fill laytime start and gross/deducted/used seconds from resolver times."""
    out = []
    for lt in ports:
        nor = _parse(lt.nor_tendered)
        turn = contract.turn_time_after_nor_hours
        start = nor + timedelta(hours=turn) if nor and turn is not None else None
        end = _parse(lt.final_hoses_disconnected)
        gross = deducted = used = None
        if start and end:
            gross = int((end - start).total_seconds())
            deducted = deducted_seconds(start, end, lt.stoppages)
        if gross is not None and deducted is not None:
            used = gross - deducted
        out.append(
            CargoPortLaytime.model_validate(
                lt.model_dump()
                | {
                    "laytime_start": start.isoformat() if start else None,
                    "gross_duration_seconds": gross,
                    "deducted_duration_seconds": deducted,
                    "used_duration_seconds": used,
                }
            )
        )
    return out


def compute_demurrage(
    contract: ContractTerms, ports: list[CargoPortLaytime]
) -> DemurrageAnswer:
    """Section 7 for reversible laytime; incomplete whenever an input is missing."""
    base = {
        "daily_rate": contract.demurrage_rate_per_day,
        "currency": contract.currency,
    }
    used = [lt.used_duration_seconds for lt in ports]
    if contract.status != "resolved" or not ports or None in used:
        return DemurrageAnswer(**base)
    if contract.reversible is False:
        return DemurrageAnswer(
            **base, calculation_formula="non-reversible laytime not supported yet"
        )
    total = sum(used)
    allowed = int(contract.laytime_allowance_hours * 3600)
    excess = max(total - allowed, 0)
    amount = round(excess / SECONDS_PER_DAY * contract.demurrage_rate_per_day, 2)
    final = all(lt.status == "final" for lt in ports)
    return DemurrageAnswer(
        **base,
        aggregation_method="reversible_cargo_ports",
        total_used_seconds=total,
        allowed_seconds=allowed,
        excess_seconds=excess,
        amount_owed=amount,
        calculation_formula="max(total_used - allowed, 0) / 86400 * daily_rate",
        status="final" if final else "provisional",
    )
