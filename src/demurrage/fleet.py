"""Fleet lookups: the active voyage, and which emails belong to it."""

from __future__ import annotations

from typing import Any

from demurrage.models import EmailExtraction, Relevance


def find_active_voyage(fleet: dict[str, Any]) -> tuple[dict, dict] | None:
    for vessel in fleet.get("vessels", []):
        if (voyage := vessel.get("current_voyage")) is not None:
            return vessel, voyage
    return None


def relevance(e: EmailExtraction, fleet: dict[str, Any]) -> Relevance:
    """Relevant only when classification ties the email to the active vessel."""
    if e.extraction_status != "complete" or e.voyage_about == "uncertain":
        return "uncertain"
    if e.voyage_about == "no":
        return "not_relevant"
    active = find_active_voyage(fleet)
    if e.vessel_name is None or active is None:
        return "uncertain"
    if e.vessel_name == active[0].get("name"):
        return "relevant"
    if e.vessel_name in {v["name"] for v in fleet.get("vessels", [])}:
        return "not_relevant"
    return "uncertain"


def extractions_for_active_voyage(
    extractions: list[EmailExtraction], fleet: dict[str, Any]
) -> list[EmailExtraction]:
    return [e for e in extractions if relevance(e, fleet) == "relevant"]
