"""Assemble VoyageAnswer: resolver core + Python demurrage/position + dump."""

from __future__ import annotations

from typing import Any

from demurrage.calculate import (
    apply_laytime_durations,
    compute_demurrage,
    pick_latest_position,
)
from demurrage.models import (
    EmailAssessment,
    EmailExtraction,
    Relevance,
    ResolvedVoyageCore,
    ReviewItem,
    VoyageAnswer,
    VoyageSummary,
)


def find_active_voyage(fleet: dict[str, Any]) -> tuple[dict, dict] | None:
    for vessel in fleet.get("vessels", []):
        if (voyage := vessel.get("current_voyage")) is not None:
            return vessel, voyage
    return None


def active_vessel_name(fleet: dict[str, Any]) -> str | None:
    active = find_active_voyage(fleet)
    if active is None:
        return None
    return str(active[0].get("name", "")) or None


def derive_relevance(e: EmailExtraction, fleet: dict[str, Any]) -> Relevance:
    if e.extraction_status != "complete":
        return e.relevance
    if e.voyage_about == "no":
        return "not_relevant"
    if e.voyage_about == "uncertain":
        return "uncertain"
    if e.vessel_name is None:
        return "uncertain"
    active = active_vessel_name(fleet)
    if active is None:
        return "uncertain"
    if e.vessel_name == active:
        return "relevant"
    fleet_names = {v["name"] for v in fleet.get("vessels", [])}
    if e.vessel_name in fleet_names:
        return "not_relevant"
    return "uncertain"


def finalize_extraction(e: EmailExtraction, fleet: dict[str, Any]) -> EmailExtraction:
    relevance = derive_relevance(e, fleet)
    if e.relevance == relevance:
        return e
    return e.model_copy(update={"relevance": relevance})


def extractions_for_active_voyage(
    extractions: list[EmailExtraction], fleet: dict[str, Any]
) -> list[EmailExtraction]:
    return [
        finalize_extraction(e, fleet)
        for e in extractions
        if derive_relevance(e, fleet) == "relevant"
    ]


def build_voyage_answer(
    fleet: dict[str, Any],
    extractions: list[EmailExtraction],
    resolved: ResolvedVoyageCore,
) -> VoyageAnswer:
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

    active = find_active_voyage(fleet)
    if active is None:
        summary = VoyageSummary(
            voyage_id="unknown",
            vessel_name="unknown",
            imo="unknown",
            charterer=None,
            status="incomplete",
        )
        review_items.append(
            ReviewItem(
                severity="blocker",
                code="NO_ACTIVE_VOYAGE",
                message="fleet.json has no vessel with current_voyage set.",
                affected_fields=["voyage"],
            )
        )
    else:
        vessel, voyage = active
        summary = VoyageSummary(
            voyage_id=str(voyage.get("voyage_no", "unknown")),
            vessel_name=str(vessel.get("name", "unknown")),
            imo=str(vessel.get("imo", "unknown")),
            charterer=voyage.get("charterer"),
            status="incomplete",
        )

    emails = [
        EmailAssessment(
            source_id=e.source_id,
            subject=e.subject,
            vessel_name=e.vessel_name,
            relevance=e.relevance,
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

    contract = resolved.contract
    port_visits = resolved.port_visits
    laytime_ports = apply_laytime_durations(contract, resolved.laytime_by_cargo_port)
    demurrage = compute_demurrage(contract, laytime_ports)
    voyage_extractions = extractions_for_active_voyage(extractions, fleet)
    position = pick_latest_position(voyage_extractions)
    blocked = any(r.severity == "blocker" for r in review_items)
    if active is not None and not blocked:
        summary = summary.model_copy(update={"status": demurrage.status})

    return VoyageAnswer(
        voyage=summary,
        emails=emails,
        contract=contract,
        port_visits=port_visits,
        laytime_by_cargo_port=laytime_ports,
        latest_position=position,
        demurrage=demurrage,
        review_items=review_items,
    )


def _hours(seconds: int | None) -> str:
    return "—" if seconds is None else f"{seconds / 3600:.2f} h"


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
        (
            "—"
            if answer.latest_position is None
            else (answer.latest_position.description or "—")
        ),
        "",
        "## Port visits",
        *(
            f"- {c.port} ({c.function}): arrival {c.arrival or '—'}, "
            f"departure {c.departure or '—'} [{c.status}]"
            for c in answer.port_visits
        ),
        *([] if answer.port_visits else ["—"]),
        "",
        "## Laytime (cargo ports)",
        *(
            f"- {lt.port}: used {_hours(lt.used_duration_seconds)} [{lt.status}]"
            for lt in answer.laytime_by_cargo_port
        ),
        *([] if answer.laytime_by_cargo_port else ["—"]),
        "",
        "## Demurrage",
        f"- Total used / allowed: {_hours(d.total_used_seconds)} / "
        f"{_hours(d.allowed_seconds)}",
        f"- Excess: {_hours(d.excess_seconds)}",
        f"- Rate: {rate}",
        f"- Amount owed: {owed} ({d.status})",
        "",
        "## Review",
        *(f"- [{r.severity}] {r.code}: {r.message}" for r in answer.review_items),
        *([] if answer.review_items else ["None."]),
        "",
    ]
    return "\n".join(lines)
