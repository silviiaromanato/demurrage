"""Voyage resolver: one LLM call returns contract, port visits, and laytime."""

from __future__ import annotations

import json
from typing import Any

from demurrage import llm
from demurrage.models import (
    CargoPortLaytime,
    ContractTerms,
    EmailExtraction,
    PortVisit,
    ResolvedVoyageCore,
)

RESOLVER_SYSTEM = (
    "From these email extractions and the planned itinerary, return the effective "
    "contract, the actual port visits, and laytime events per cargo port. Cite "
    "evidence quotes from the facts. Leave all *_duration_seconds null. Ask a "
    "question instead of guessing between equal-authority sources."
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
    """One LLM call per voyage rebuild."""
    skeleton = itinerary_skeleton(voyage)
    if not llm.is_configured():
        return skeleton
    user = json.dumps(
        {
            "itinerary": skeleton.model_dump(include={"port_visits"}),
            "extractions": [e.model_dump() for e in extractions],
        }
    )
    return llm.structured_call(RESOLVER_SYSTEM, user, ResolvedVoyageCore)
