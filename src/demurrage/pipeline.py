"""Ingest pipeline and voyage-answer pipeline, plus the `demurrage` CLI.

Batch and live only record extractions. The resolver runs when the user
asks for the voyage answer (`demurrage report <voyage number or vessel name>`).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from demurrage.answer import build_voyage_answer, render_brief
from demurrage.classify import extract
from demurrage.fleet import UnknownVoyage, emails_for_voyage_no, find_voyage
from demurrage.llm import cost_summary
from demurrage.models import (
    EmailExtraction,
    EmailReview,
    ResolvedVoyageCore,
    VoyageAnswer,
)
from demurrage.parse import parse_eml_file, source_id_for
from demurrage.resolver import bodies_for, itinerary_skeleton, resolve
from demurrage.reviews import advance_extraction, replay_reviews, review_for
from demurrage.validate import (
    require,
    validate_answer,
    validate_brief,
    validate_extraction,
    validate_extraction_log,
    validate_resolved,
)

EMAILS_DIR = Path("emails")
FLEET_PATH = Path("fleet.json")
EXTRACTIONS_PATH = Path("extractions.jsonl")
RESULT_PATH = Path("result.json")
BRIEF_PATH = Path("brief.md")
RESOLVED_PATH = Path("resolved.json")
MATERIAL_TOPICS = ("contract", "schedule", "laytime", "position")


def read_extractions() -> list[EmailExtraction]:
    if not EXTRACTIONS_PATH.is_file():
        return []
    lines = EXTRACTIONS_PATH.read_text(encoding="utf-8").splitlines()
    return [EmailExtraction.model_validate_json(line) for line in lines if line.strip()]


def _line(e: EmailExtraction) -> str:
    return e.model_dump_json() + "\n"


def write_extractions(extractions: list[EmailExtraction]) -> None:
    EXTRACTIONS_PATH.write_text("".join(map(_line, extractions)), encoding="utf-8")


def open_reviews(extractions: list[EmailExtraction]) -> list[EmailExtraction]:
    return [e for e in extractions if e.review and e.review.status == "open"]


def load_fleet() -> dict[str, Any]:
    return json.loads(FLEET_PATH.read_text(encoding="utf-8"))


def needs_resolve(extractions: list[EmailExtraction]) -> bool:
    """True when an email can change contract, schedule, laytime, or position."""
    return any(
        e.topics.get(topic) == "yes" for e in extractions for topic in MATERIAL_TOPICS
    )


def _core_for(
    vessel: dict[str, Any],
    voyage: dict[str, Any],
    voyage_emails: list[EmailExtraction],
) -> ResolvedVoyageCore:
    """Contract call, then laytime call.

    Reuse a saved core when nothing material changed.
    """
    if needs_resolve(voyage_emails):
        resolved = resolve(voyage, voyage_emails, vessel=vessel)
        RESOLVED_PATH.write_text(
            resolved.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return resolved
    if RESOLVED_PATH.is_file():
        return ResolvedVoyageCore.model_validate_json(
            RESOLVED_PATH.read_text(encoding="utf-8")
        )
    return itinerary_skeleton(voyage)


def write_outputs(identifier: str) -> VoyageAnswer:
    """Resolve the voyage, validate, then write result.json and brief.md.

    ``identifier`` is a voyage number or a vessel name.
    """
    fleet = load_fleet()
    extractions = read_extractions()
    try:
        vessel, voyage = find_voyage(fleet, identifier)
    except UnknownVoyage as exc:
        raise SystemExit(str(exc)) from exc
    voyage_emails = emails_for_voyage_no(
        extractions, fleet, str(voyage.get("voyage_no", ""))
    )
    resolved = _core_for(vessel, voyage, voyage_emails)
    require(
        "resolve",
        validate_resolved(resolved, voyage, voyage_emails, bodies_for(voyage_emails)),
    )
    answer = build_voyage_answer(fleet, extractions, resolved, vessel, voyage)
    require("answer", validate_answer(answer, extractions))
    RESULT_PATH.write_text(answer.model_dump_json(indent=2) + "\n", encoding="utf-8")
    BRIEF_PATH.write_text(render_brief(answer), encoding="utf-8")
    return answer


def ingest(
    path: Path, known: set[str], prior_answers: dict[str, list[EmailReview]]
) -> EmailExtraction | None:
    """Parse, extract and append one email; skip if already recorded."""
    source_id = source_id_for(path)
    if source_id in known:
        return None
    parsed = parse_eml_file(path)
    fleet = load_fleet()
    extraction = extract(parsed, fleet)
    require(f"extract {path.name}", validate_extraction(extraction, parsed))
    priors = prior_answers.get(source_id, [])
    extraction = replay_reviews(extraction, priors)
    extraction = extraction.model_copy(
        update={"answered_reviews": priors, "review": review_for(extraction, fleet)}
    )
    with EXTRACTIONS_PATH.open("a", encoding="utf-8") as f:
        f.write(_line(extraction))
    known.add(source_id)
    return extraction


def run_live(
    email_paths: list[Path] | None = None,
    prior_answers: dict[str, list[EmailReview]] | None = None,
) -> int:
    """Ingest emails in order, stopping at the first one that needs a human answer."""
    recorded = read_extractions()
    if blocked := open_reviews(recorded):
        print(f"live: waiting for an answer on {blocked[0].filename}")
        return 0
    known = {e.source_id for e in recorded}
    added = 0
    for path in email_paths or sorted(EMAILS_DIR.glob("*.eml")):
        extraction = ingest(path, known, prior_answers or {})
        if extraction is None:
            continue
        added += 1
        if extraction.extraction_status == "pending" and extraction.warnings:
            print(f"live: {extraction.filename}: {extraction.warnings[-1]}")
        if extraction.review and extraction.review.status == "open":
            print(
                f"live: paused on {extraction.filename}: {extraction.review.prompt} "
                f"Run: demurrage answer {extraction.filename} <one of "
                f"{extraction.review.options}>"
            )
            break
    print(f"live: appended {added} new emails")
    return added


def run_batch() -> None:
    """Rebuild extractions from scratch, reusing human answers for the same email.

    Does not call the resolver. A previous voyage answer is removed so it
    cannot be read against the rebuilt log; run `report` to build a new one.
    """
    prior_answers = {e.source_id: e.answered_reviews for e in read_extractions()}
    for path in (EXTRACTIONS_PATH, RESOLVED_PATH, RESULT_PATH, BRIEF_PATH):
        path.unlink(missing_ok=True)
    run_live(prior_answers=prior_answers)


def run_report(identifier: str) -> VoyageAnswer:
    """Resolve the named voyage and write result.json and brief.md.

    ``identifier`` is a voyage number or a vessel name.
    """
    answer = write_outputs(identifier)
    voyage = answer.voyage
    print(
        "report: wrote result.json and brief.md "
        f"({voyage.vessel_name} {voyage.voyage_id}, {voyage.status})"
    )
    return answer


def run_answer(email: str, answer: str) -> None:
    """Answer the open question on one email's line, then resume ingest."""
    extractions = read_extractions()
    matches = [
        i
        for i, e in enumerate(extractions)
        if e.review and e.review.status == "open" and email in (e.source_id, e.filename)
    ]
    if not matches:
        raise SystemExit(f"No open question for {email!r}")
    i = matches[0]
    review = extractions[i].review
    if answer not in review.options:
        raise SystemExit(f"Answer must be one of {review.options}")
    answered = EmailReview.model_validate(
        review.model_dump()
        | {
            "status": "answered",
            "answer": answer,
            "answered_at": datetime.now(UTC).isoformat(),
        }
    )
    fleet = load_fleet()
    extractions[i] = advance_extraction(
        extractions[i],
        answered,
        fleet,
        [*extractions[i].answered_reviews, answered],
    )
    write_extractions(extractions)
    print(f"answer: recorded for {extractions[i].filename}; resuming")
    run_live()


def run_check() -> list[str]:
    """Re-validate every saved output against its schema and against each other."""
    try:
        extractions = read_extractions()
        answer = VoyageAnswer.model_validate_json(RESULT_PATH.read_text("utf-8"))
    except (OSError, ValidationError) as exc:
        problems = [f"load: {exc}"]
    else:
        problems = validate_extraction_log(extractions)
        for e in extractions:
            path = EMAILS_DIR / e.filename
            if not path.is_file():
                problems.append(f"{e.filename}: email file not found")
            else:
                parsed = parse_eml_file(path)
                problems += [
                    f"{e.filename}: {p}" for p in validate_extraction(e, parsed)
                ]
        try:
            _vessel, voyage = find_voyage(load_fleet(), answer.voyage.voyage_id)
        except UnknownVoyage:
            if answer.voyage.voyage_id != "unknown":
                problems.append(
                    f"voyage {answer.voyage.voyage_id!r} is not a current voyage "
                    "in fleet.json"
                )
        else:
            core = ResolvedVoyageCore(
                contract=answer.contract,
                port_visits=answer.port_visits,
                laytime_by_cargo_port=[
                    lt.model_copy(
                        update=dict.fromkeys(
                            (
                                "gross_duration_seconds",
                                "deducted_duration_seconds",
                                "used_duration_seconds",
                            )
                        )
                    )
                    for lt in answer.laytime_by_cargo_port
                ],
                latest_position=answer.latest_position,
            )
            problems += validate_resolved(
                core, voyage, extractions, bodies_for(extractions)
            )
        problems += validate_answer(answer, extractions)
        if BRIEF_PATH.is_file():
            problems += validate_brief(BRIEF_PATH.read_text("utf-8"), answer)
        else:
            problems.append("brief.md not found")
    for p in problems:
        print(f"check: {p}")
    print(f"check: {'OK' if not problems else f'{len(problems)} problem(s)'}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Demurrage ingest and voyage-answer pipelines"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("batch", help="Classify all emails in emails/ from scratch")
    live = sub.add_parser("live", help="Append new emails to extractions.jsonl")
    live.add_argument(
        "email_paths",
        nargs="*",
        type=Path,
        help="Optional .eml paths; if omitted, scans emails/ for unrecorded files",
    )
    answer = sub.add_parser("answer", help="Answer the open question on an email")
    answer.add_argument("email", help="filename or source_id of the email")
    answer.add_argument("answer", help="one of the options listed in the question")
    report = sub.add_parser(
        "report",
        help="Resolve a voyage and write result.json and brief.md",
    )
    report.add_argument(
        "voyage",
        help="Voyage number (KB-2603) or vessel name (KESTREL BAY)",
    )
    sub.add_parser("check", help="Validate saved outputs against each other")

    args = parser.parse_args()
    try:
        if args.command == "batch":
            run_batch()
        elif args.command == "live":
            run_live(args.email_paths)
        elif args.command == "answer":
            run_answer(args.email, args.answer)
        elif args.command == "report":
            run_report(args.voyage)
        elif run_check():
            raise SystemExit(1)
    finally:
        if text := cost_summary():
            print(text)
