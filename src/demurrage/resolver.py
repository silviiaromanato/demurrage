"""Voyage resolver: planned itinerary now; a generative model comes later."""

from __future__ import annotations

from typing import Any

from demurrage.models import (
    CargoPortLaytime,
    ContractTerms,
    EmailExtraction,
    PortVisit,
    ResolvedVoyageCore,
)


def visit_id_for(item: dict[str, Any]) -> str:
    seq = item.get("seq")
    return f"pv-{seq}" if seq is not None else f"pv-{item.get('port', 'unknown')}"


def itinerary_skeleton(voyage: dict[str, Any]) -> ResolvedVoyageCore:
    """The planned ports with every value missing; also the fallback with no LLM."""
    visits, laytime = [], []
    for item in voyage.get("itinerary", []):
        visit_id = visit_id_for(item)
        port = str(item.get("port", "unknown"))
        function = str(item.get("function", "other"))
        applies = function in ("load", "discharge")
        visits.append(
            PortVisit(
                visit_id=visit_id, port=port, function=function, laytime_applies=applies
            )
        )
        if applies:
            laytime.append(CargoPortLaytime(visit_id=visit_id, port=port))
    return ResolvedVoyageCore(
        contract=ContractTerms(status="missing"),
        port_visits=visits,
        laytime_by_cargo_port=laytime,
    )


def resolve(
    voyage: dict[str, Any], extractions: list[EmailExtraction]
) -> ResolvedVoyageCore:
    """Jev only answers choice questions; the resolver still uses the itinerary."""
    return itinerary_skeleton(voyage)
