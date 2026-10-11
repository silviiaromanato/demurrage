"""Cross-stage output checks. Each returns a list of problems; empty means valid."""

from __future__ import annotations

from typing import Any

from src.answer import render_brief
from src.calculate import apply_laytime_durations, compute_demurrage
from src.derive import TIME_FIELDS, placed_time, timed_rows
from src.models import (
    EmailExtraction,
    EvidenceRef,
    ParsedEmail,
    ResolvedVoyageCore,
    VoyageAnswer,
)
from src.resolver import (
    cited_text,
    evidence_refs,
    itinerary_skeleton,
    port_offsets,
)


class StageValidationError(Exception):
    def __init__(self, stage: str, problems: list[str]):
        self.stage, self.problems = stage, problems
        super().__init__(f"{stage}: " + "; ".join(problems))


def require(stage: str, problems: list[str]) -> None:
    if problems:
        raise StageValidationError(stage, problems)


def validate_extraction(e: EmailExtraction, parsed: ParsedEmail) -> list[str]:
    """The extraction belongs to this email."""
    return [
        f"{key} {getattr(e, key)!r} != parsed {getattr(parsed, key)!r}"
        for key in ("source_id", "filename", "content_hash", "message_id")
        if getattr(e, key) != getattr(parsed, key)
    ]


def _citation_problem(
    where: str, ref: EvidenceRef, bodies: dict[str, str]
) -> str | None:
    body = bodies.get(ref.source_id)
    if body is None:
        return f"{where}: no body for {ref.source_id}"
    text = cited_text(body, ref.first_line, ref.last_line)
    if text is None:
        return (
            f"{where}: lines {ref.first_line}-{ref.last_line} are outside "
            f"{ref.source_id} ({len(body.splitlines())} lines)"
        )
    if ref.quote != text:
        return f"{where}: quote does not match lines of {ref.source_id}"
    return None


def validate_resolved(
    core: ResolvedVoyageCore,
    voyage: dict[str, Any],
    extractions: list[EmailExtraction],
    bodies: dict[str, str] | None = None,
) -> list[str]:
    """Resolver output matches the itinerary and cites known emails."""
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

    rules = {r.kind: r.fraction_deducted for r in core.contract.deduction_rules}
    for lt in core.laytime_by_cargo_port:
        for stop in lt.stoppages:
            rule = stop.deduction_rule
            if rule is not None and rule not in rules:
                problems.append(f"stoppage {lt.visit_id}: unknown rule {rule!r}")
            elif stop.fraction_deducted != (0.0 if rule is None else rules[rule]):
                problems.append(
                    f"stoppage {lt.visit_id}: fraction_deducted "
                    f"{stop.fraction_deducted} does not follow rule {rule!r}"
                )

    offsets = port_offsets(voyage)
    for where, row, visit_id in timed_rows(core):
        for name in TIME_FIELDS[type(row)]:
            written = getattr(row, f"{name}_written")
            if written is None:
                continue
            placed = placed_time(written, offsets.get(visit_id) if visit_id else None)
            if placed is None and visit_id is not None:
                problems.append(
                    f"{where}: {name}_written {written.value!r} is not "
                    "YYYY-MM-DDTHH:MM without an offset"
                )
            elif getattr(row, name) != placed:
                problems.append(f"{where}: {name} does not follow {name}_written")

    known = {e.source_id for e in extractions}
    for where, ref in evidence_refs(core):
        if ref.source_id not in known:
            problems.append(f"{where}: evidence cites unknown {ref.source_id}")
        elif bodies is not None and (p := _citation_problem(where, ref, bodies)):
            problems.append(p)
    return problems


def validate_answer(
    answer: VoyageAnswer, extractions: list[EmailExtraction]
) -> list[str]:
    """The answer covers every email and its numbers recompute exactly."""
    problems = []
    if [a.source_id for a in answer.emails] != [e.source_id for e in extractions]:
        problems.append("emails[] does not match outputs/extractions.jsonl order/content")

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
        else ["outputs/brief.md != render_brief(outputs/result.json)"]
    )
