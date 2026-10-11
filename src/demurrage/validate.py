"""Cross-stage output checks. Each returns a list of problems; empty means valid."""

from __future__ import annotations

import re
from typing import Any

from demurrage.answer import render_brief
from demurrage.calculate import apply_laytime_durations, compute_demurrage
from demurrage.models import (
    EmailExtraction,
    EvidenceRef,
    ParsedEmail,
    ResolvedVoyageCore,
    VoyageAnswer,
)
from demurrage.resolver import itinerary_skeleton


class StageValidationError(Exception):
    def __init__(self, stage: str, problems: list[str]):
        self.stage, self.problems = stage, problems
        super().__init__(f"{stage}: " + "; ".join(problems))


def require(stage: str, problems: list[str]) -> None:
    if problems:
        raise StageValidationError(stage, problems)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def validate_extraction(e: EmailExtraction, parsed: ParsedEmail) -> list[str]:
    """The extraction belongs to this email and every quote is in its body."""
    problems = [
        f"{key} {getattr(e, key)!r} != parsed {getattr(parsed, key)!r}"
        for key in ("source_id", "filename", "content_hash", "message_id")
        if getattr(e, key) != getattr(parsed, key)
    ]
    body = _norm(parsed.body_text)
    problems += [
        f"{f.fact_id}: quote not found in email body: {f.quote!r}"
        for f in e.all_facts()
        if _norm(f.quote) not in body
    ]
    return problems


def _evidence_problems(
    where: str, refs: list[EvidenceRef], extractions: list[EmailExtraction]
) -> list[str]:
    quotes = {e.source_id: [_norm(f.quote) for f in e.all_facts()] for e in extractions}
    problems = []
    for ref in refs:
        if ref.source_id not in quotes:
            problems.append(f"{where}: evidence cites unknown {ref.source_id}")
        elif not any(_norm(ref.quote) in q for q in quotes[ref.source_id]):
            problems.append(f"{where}: quote not among extracted facts: {ref.quote!r}")
    return problems


def validate_resolved(
    core: ResolvedVoyageCore,
    voyage: dict[str, Any],
    extractions: list[EmailExtraction],
) -> list[str]:
    """Resolver output matches the itinerary and only cites extracted facts."""
    problems = []
    visit_ids = [v.visit_id for v in core.port_visits]
    if len(visit_ids) != len(set(visit_ids)):
        problems.append("duplicate visit_id in port_visits")
    planned = {v.visit_id for v in itinerary_skeleton(voyage).port_visits}
    if missing := planned - set(visit_ids):
        problems.append(f"planned visits missing: {sorted(missing)}")

    cargo = {v.visit_id for v in core.port_visits if v.laytime_applies}
    rows = [lt.visit_id for lt in core.laytime_by_cargo_port]
    if sorted(rows) != sorted(cargo):
        problems.append(f"laytime rows {sorted(rows)} != cargo visits {sorted(cargo)}")
    for lt in core.laytime_by_cargo_port:
        filled = [
            k
            for k in ("gross", "deducted", "used")
            if getattr(lt, f"{k}_duration_seconds") is not None
        ]
        if filled:
            problems.append(f"{lt.visit_id}: resolver must not fill {filled} durations")

    problems += _evidence_problems("contract", core.contract.evidence, extractions)
    for v in core.port_visits:
        problems += _evidence_problems(v.visit_id, v.evidence, extractions)
    for lt in core.laytime_by_cargo_port:
        problems += _evidence_problems(
            f"laytime {lt.visit_id}", lt.evidence, extractions
        )
    return problems


def validate_answer(
    answer: VoyageAnswer, extractions: list[EmailExtraction]
) -> list[str]:
    """The answer covers every email and its numbers recompute exactly."""
    problems = []
    if [a.source_id for a in answer.emails] != [e.source_id for e in extractions]:
        problems.append("emails[] does not match extractions.jsonl order/content")

    recomputed = apply_laytime_durations(answer.contract, answer.laytime_by_cargo_port)
    for got, want in zip(answer.laytime_by_cargo_port, recomputed, strict=True):
        if got != want:
            problems.append(f"laytime {got.visit_id} does not recompute")
    if answer.demurrage != compute_demurrage(answer.contract, recomputed):
        problems.append("demurrage does not recompute from contract + laytime")

    flagged = {sid for r in answer.review_items for sid in r.related_source_ids}
    problems += [
        f"open question on {e.filename} has no review item"
        for e in extractions
        if e.review and e.review.status == "open" and e.source_id not in flagged
    ]
    if answer.voyage.status == "final" and answer.demurrage.status != "final":
        problems.append("voyage is final but demurrage is not")
    return problems


def validate_extraction_log(extractions: list[EmailExtraction]) -> list[str]:
    """One line per email, and only the last line may hold an open question."""
    ids = [e.source_id for e in extractions]
    problems = [f"duplicate line for {s}" for s in sorted(set(ids)) if ids.count(s) > 1]
    open_at = [
        i for i, e in enumerate(extractions) if e.review and e.review.status == "open"
    ]
    if open_at and open_at != [len(extractions) - 1]:
        problems.append("open question must be on the last line (ingest is sequential)")
    return problems


def validate_brief(brief: str, answer: VoyageAnswer) -> list[str]:
    return (
        []
        if brief == render_brief(answer)
        else ["brief.md != render_brief(result.json)"]
    )
