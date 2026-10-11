"""Assemble VoyageAnswer: resolver core + Python demurrage + brief."""

from __future__ import annotations

from typing import Any

from demurrage.calculate import apply_laytime_durations, compute_demurrage
from demurrage.fleet import relevance
from demurrage.models import (
    CargoPortLaytime,
    EmailAssessment,
    EmailExtraction,
    EvidenceRef,
    PortVisit,
    ResolvedVoyageCore,
    ReviewItem,
    Stoppage,
    VoyageAnswer,
    VoyageSummary,
)


def build_voyage_answer(
    fleet: dict[str, Any],
    extractions: list[EmailExtraction],
    resolved: ResolvedVoyageCore,
    vessel: dict[str, Any],
    voyage: dict[str, Any],
) -> VoyageAnswer:
    """Resolver core plus Python durations, demurrage, email list and reviews."""
    review_items = [
        ReviewItem(
            severity="blocker",
            code=e.review.code,
            message=(
                f"{e.review.prompt} Answer with: demurrage answer {e.filename} "
                f"<one of {e.review.options}>"
            ),
            affected_fields=["emails"],
            related_source_ids=[e.source_id],
        )
        for e in extractions
        if e.review and e.review.status == "open"
    ]

    pending_ids = [e.source_id for e in extractions if e.extraction_status == "pending"]
    if pending_ids:
        review_items.append(
            ReviewItem(
                severity="material",
                code="EXTRACTION_PENDING",
                message=(
                    "One or more emails have pending LLM extraction; "
                    "contract, laytime, and demurrage fields are not populated."
                ),
                affected_fields=[
                    "contract",
                    "port_visits",
                    "laytime_by_cargo_port",
                    "demurrage",
                ],
                related_source_ids=pending_ids,
            )
        )

    review_items.extend(
        ReviewItem(
            severity="blocker",
            code="RESOLVER_QUESTION",
            message=f"{q.prompt} Options: {q.options}",
            affected_fields=["contract", "port_visits", "laytime_by_cargo_port"],
        )
        for q in resolved.questions
    )

    contract = resolved.contract
    laytime_ports = apply_laytime_durations(contract, resolved.laytime_by_cargo_port)
    demurrage = compute_demurrage(contract, laytime_ports)
    blocked = any(r.severity == "blocker" for r in review_items)
    summary = VoyageSummary(
        voyage_id=str(voyage.get("voyage_no", "unknown")),
        vessel_name=str(vessel.get("name", "unknown")),
        imo=str(vessel.get("imo", "unknown")),
        charterer=voyage.get("charterer"),
        status="incomplete" if blocked else demurrage.status,
    )

    emails = [
        EmailAssessment(
            source_id=e.source_id,
            subject=e.subject,
            vessel_name=e.vessel_name,
            relevance=relevance(e, fleet, vessel_name=summary.vessel_name),
            affects_schedule=e.affects_schedule,
            affects_laytime=e.affects_laytime,
            topics=e.topics,
            reason=(
                "Pending LLM extraction."
                if e.extraction_status == "pending"
                else f"Classified: about a voyage = {e.voyage_about}."
            ),
        )
        for e in extractions
    ]

    return VoyageAnswer(
        voyage=summary,
        emails=emails,
        contract=contract,
        port_visits=resolved.port_visits,
        laytime_by_cargo_port=laytime_ports,
        latest_position=resolved.latest_position,
        demurrage=demurrage,
        review_items=review_items,
    )


def _hours(seconds: int | None) -> str:
    return "—" if seconds is None else f"{seconds / 3600:.2f} h"


def _evidence_lines(refs: list[EvidenceRef], indent: str = "  ") -> list[str]:
    """The source emails behind a fact, once each; quotes stay in result.json."""
    sources = dict.fromkeys(ref.source_id for ref in refs)
    if not sources:
        return []
    return [f"{indent}- source: " + ", ".join(f"`{s}`" for s in sources)]


def _position_lines(answer: VoyageAnswer) -> list[str]:
    if answer.latest_position is None:
        return ["—"]
    lines = [answer.latest_position.description or "—"]
    lines.extend(_evidence_lines(answer.latest_position.evidence, indent=""))
    return lines


def _visit_lines(visits: list[PortVisit]) -> list[str]:
    if not visits:
        return ["—"]
    lines: list[str] = []
    for visit in visits:
        lines.append(
            f"- {visit.port} ({visit.function}): arrival {visit.arrival or '—'}, "
            f"departure {visit.departure or '—'} [{visit.status}]"
        )
        lines.extend(_evidence_lines(visit.evidence))
    return lines


def _stoppage_lines(stop: Stoppage) -> list[str]:
    deducted = (
        "—" if stop.fraction_deducted is None else f"{stop.fraction_deducted:.0%}"
    )
    rule = f", rule: {stop.deduction_rule}" if stop.deduction_rule else ""
    lines = [
        f"  - {stop.description} ({stop.cause}, {deducted} deducted{rule}): "
        f"{stop.start or '—'} to {stop.end or '—'}"
    ]
    lines.extend(_evidence_lines(stop.evidence, indent="    "))
    return lines


def _laytime_lines(ports: list[CargoPortLaytime]) -> list[str]:
    if not ports:
        return ["—"]
    lines: list[str] = []
    for lt in ports:
        lines.append(
            f"- {lt.port}: used {_hours(lt.used_duration_seconds)} [{lt.status}]"
        )
        lines.extend(_evidence_lines(lt.evidence))
        for stop in lt.stoppages:
            lines.extend(_stoppage_lines(stop))
    return lines


def render_brief(answer: VoyageAnswer) -> str:
    v, d = answer.voyage, answer.demurrage
    rate = f"{d.daily_rate:,.2f} {d.currency or ''}/day" if d.daily_rate else "—"
    owed = (
        f"{d.amount_owed:,.2f} {d.currency or ''}" if d.amount_owed is not None else "—"
    )
    lines = [
        "# Voyage brief",
        "",
        f"**Vessel:** {v.vessel_name} (IMO {v.imo})",
        f"**Voyage:** {v.voyage_id}",
        f"**Charterer:** {v.charterer or '—'}",
        f"**Answer status:** {v.status}",
        "",
        "## Position",
        *_position_lines(answer),
        "",
        "## Port visits",
        *_visit_lines(answer.port_visits),
        "",
        "## Laytime (cargo ports)",
        *_laytime_lines(answer.laytime_by_cargo_port),
        "",
        "## Demurrage",
        f"- Total used / allowed: {_hours(d.total_used_seconds)} / "
        f"{_hours(d.allowed_seconds)}",
        f"- Excess: {_hours(d.excess_seconds)}",
        f"- Rate: {rate}",
        f"- Amount owed: {owed} ({d.status})",
        *_evidence_lines(answer.contract.evidence, indent=""),
        "",
        "## Review",
        *(f"- [{r.severity}] {r.code}: {r.message}" for r in answer.review_items),
        *([] if answer.review_items else ["None."]),
        "",
    ]
    return "\n".join(lines)
