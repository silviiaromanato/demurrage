"""Python-only arithmetic: laytime durations, demurrage, latest position."""

from __future__ import annotations

from datetime import datetime, timedelta

from demurrage.models import (
    CargoPortLaytime,
    ContractTerms,
    DemurrageAnswer,
    EmailExtraction,
    LatestPosition,
)

SECONDS_PER_DAY = 86400


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def apply_laytime_durations(
    contract: ContractTerms, ports: list[CargoPortLaytime]
) -> list[CargoPortLaytime]:
    """Fill gross/deducted/used seconds from the resolver's timestamps.

    Stoppages are summed as given; clipping to the laytime window and splitting
    overlaps (section 7) are not implemented yet.
    """
    out = []
    for lt in ports:
        start = _parse(lt.laytime_start)
        nor = _parse(lt.nor_tendered)
        if start is None and nor and contract.turn_time_after_nor_hours is not None:
            start = nor + timedelta(hours=contract.turn_time_after_nor_hours)
        end = _parse(lt.final_hoses_disconnected)
        gross = int((end - start).total_seconds()) if start and end else None
        durations = [s.duration_seconds for s in lt.stoppages]
        deducted = sum(durations) if None not in durations else None
        used = gross - deducted if gross is not None and deducted is not None else None
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
        evidence=contract.evidence,
    )


def pick_latest_position(extractions: list[EmailExtraction]) -> LatestPosition | None:
    """Latest supported position from `position_facts` (not implemented yet)."""
    return None
