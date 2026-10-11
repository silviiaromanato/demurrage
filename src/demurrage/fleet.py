"""Fleet lookups: a voyage by number or vessel name, and which emails belong to it."""

from __future__ import annotations

from typing import Any

from demurrage.models import EmailExtraction, Relevance


class UnknownVoyage(LookupError):
    """The identifier is not a current voyage number or a vessel on a voyage."""


def find_active_voyage(fleet: dict[str, Any]) -> tuple[dict, dict] | None:
    for vessel in fleet.get("vessels", []):
        if (voyage := vessel.get("current_voyage")) is not None:
            return vessel, voyage
    return None


def find_voyage(fleet: dict[str, Any], identifier: str) -> tuple[dict, dict]:
    """Return ``(vessel, current_voyage)`` for a voyage number or a vessel name.

    A voyage number is tried first, so it wins when the same text is also a
    vessel name. Matching ignores case and surrounding whitespace.
    """
    key = identifier.strip().casefold()
    if not key:
        raise UnknownVoyage("Pass a voyage number or a vessel name.")
    named: dict[str, Any] | None = None
    for vessel in fleet.get("vessels", []):
        voyage = vessel.get("current_voyage")
        number = str((voyage or {}).get("voyage_no", "")).casefold()
        if voyage is not None and number == key:
            return vessel, voyage
        if str(vessel.get("name", "")).casefold() == key:
            named = vessel
    if named is not None:
        voyage = named.get("current_voyage")
        if voyage is not None:
            return named, voyage
        raise UnknownVoyage(f"{named.get('name')} has no current voyage.")
    raise UnknownVoyage(f"No voyage number or vessel name matches {identifier!r}.")


def relevance(
    e: EmailExtraction,
    fleet: dict[str, Any],
    vessel_name: str | None = None,
) -> Relevance:
    """Relevant only when classification ties the email to this vessel.

    With no vessel name, the vessel on a current voyage is used.
    """
    if e.extraction_status != "complete" or e.voyage_about == "uncertain":
        return "uncertain"
    if e.voyage_about == "no":
        return "not_relevant"
    if vessel_name is None:
        active = find_active_voyage(fleet)
        vessel_name = active[0].get("name") if active else None
    if e.vessel_name is None or not isinstance(vessel_name, str):
        return "uncertain"
    if e.vessel_name == vessel_name:
        return "relevant"
    if e.vessel_name in {v["name"] for v in fleet.get("vessels", [])}:
        return "not_relevant"
    return "uncertain"


def extractions_for_active_voyage(
    extractions: list[EmailExtraction],
    fleet: dict[str, Any],
    vessel_name: str | None = None,
) -> list[EmailExtraction]:
    return [
        e
        for e in extractions
        if relevance(e, fleet, vessel_name=vessel_name) == "relevant"
    ]


def emails_for_voyage_no(
    extractions: list[EmailExtraction], fleet: dict[str, Any], voyage_no: str
) -> list[EmailExtraction]:
    """Emails classified onto the vessel whose current voyage has this number.

    Classification records the vessel, not the voyage number, so the number
    selects that vessel's relevant emails.
    """
    vessel_name = None
    for vessel in fleet.get("vessels", []):
        voyage = vessel.get("current_voyage") or {}
        if str(voyage.get("voyage_no", "")) == str(voyage_no):
            vessel_name = vessel.get("name")
            break
    if vessel_name is None:
        return []
    return [
        e
        for e in extractions
        if e.extraction_status == "complete"
        and e.voyage_about == "yes"
        and e.vessel_name == vessel_name
    ]
