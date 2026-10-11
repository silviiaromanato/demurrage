"""Voyage resolver: contract call, then laytime call. Python fills durations."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from src import llm
from src.derive import apply_rule_fractions, finish_contract, place_times
from src.models import (
    CargoPortLaytime,
    ContractDraft,
    ContractTerms,
    EmailExtraction,
    EvidenceRef,
    LaytimeDraft,
    PortVisit,
    ResolvedVoyageCore,
    ResolverQuestion,
)
from src.parse import parse_eml_file

EMAILS_DIR = Path("emails")

CITATION_RULE = """\
- Every material value needs an EvidenceRef: the source_id of the email and
  the first_line and last_line numbers of the body lines that state it
  (body_lines are numbered "N| text"). Cite the fewest lines that support
  the value, usually one; a clause wrapped over two lines is two lines.
- Do no arithmetic. Copy numbers, dates and times as the email writes them;
  Python converts units and time zones and computes every duration.
"""

CONTRACT_PROMPT = f"""\
You extract the contract terms for one tanker voyage from the contract emails.

Rules:
- A clean recap supplies the baseline contract.
- An addendum replaces only the term it explicitly changes.
- Email delivery order never decides which value wins.
- Equal-authority disagreement stays conflicted: add a question, do not pick.
- Losing and superseded evidence stays visible in notes.
{CITATION_RULE}- The laytime allowance is the number and unit as written (e.g. 48 hours,
  3 days); do not convert days to hours.
- Do not fill laytime durations or demurrage.
"""

LAYTIME_PROMPT = f"""\
You fill port visits, cargo laytime, and the latest position for one
tanker voyage. Deduction rules are already decided; name the one that
applies to each stoppage and do not rewrite them.

Rules:
- Actual events beat estimates.
- A final SOF beats an interim SOF and ordinary notices for that port.
- An interim SOF may support a provisional answer before the final SOF.
- Equivalent UTC and local times are one event, not a conflict.
- Equal-authority disagreement stays conflicted: add a question, do not pick.
- A non-cargo call stays on the timeline with laytime_applies false.
- Email delivery order never decides which value wins.
- Losing and superseded evidence stays visible in notes.
{CITATION_RULE}- Use visit_id from the itinerary. One port_visits row per visit, in
  order. One laytime row per load or discharge visit.
- A stoppage's deduction_rule is the kind of the supplied rule that applies,
  or null if none applies (the stoppage then counts in full as laytime).
  Delays caused by the terminal or by waiting for a berth normally count
  in full unless a supplied rule says otherwise.
- Every time goes in a *_written field. value is the date and clock as the
  cited line writes them, formatted YYYY-MM-DDTHH:MM with no offset
  (e.g. "15/1630" is the 15th of the current month at 16:30). Do not convert.
- basis is who wrote the time, not a calculation:
  SOFs and agents' notices are local time of the port (basis local), even
  when they say WAT or CET.
  Masters often write times in UTC, also written GMT or Z (basis utc)
  when so labelled. Check the label. A compact "15/1630" labelled UTC
  is 16:30 UTC.
  The email Date header is when the mail was sent; it is not an event time.
- latest_position is the position report with the latest reported_at
  (when the vessel was observed there). Do not use the email Date header
  or sent_at. Null if no position report exists.
- Do not fill duration seconds or demurrage.
"""


def visit_id_for(item: dict[str, Any]) -> str:
    seq = item.get("seq")
    return f"pv-{seq}" if seq is not None else f"pv-{item.get('port', 'unknown')}"


def port_offsets(voyage: dict[str, Any]) -> dict[str, str | None]:
    """visit_id -> the port's UTC offset from fleet.json (e.g. '+01:00')."""
    return {
        visit_id_for(item): item.get("utc_offset")
        for item in voyage.get("itinerary", [])
    }


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


def email_body(filename: str) -> str:
    path = EMAILS_DIR / filename
    if not path.is_file():
        return ""
    return parse_eml_file(path).body_text


def bodies_for(extractions: list[EmailExtraction]) -> dict[str, str]:
    return {e.source_id: email_body(e.filename) for e in extractions}


def numbered_lines(body: str) -> list[str]:
    return [f"{n}| {line}" for n, line in enumerate(body.splitlines(), start=1)]


def cited_text(body: str, first_line: int, last_line: int) -> str | None:
    """Body lines first_line..last_line (1-based, inclusive); None if out of range."""
    lines = body.splitlines()
    if not 1 <= first_line <= last_line <= len(lines):
        return None
    return "\n".join(lines[first_line - 1 : last_line])


def contract_refs(contract: ContractTerms) -> Iterator[tuple[str, EvidenceRef]]:
    for ref in contract.evidence:
        yield "contract", ref
    for rule in contract.deduction_rules:
        yield f"deduction {rule.kind}", rule.evidence
    for amendment in contract.amendments:
        yield f"amendment {amendment.term}", amendment.evidence


def evidence_refs(core: Any) -> Iterator[tuple[str, EvidenceRef]]:
    """Every EvidenceRef on a resolved core or voyage answer, with where it sits."""
    yield from contract_refs(core.contract)
    for visit in core.port_visits:
        for ref in visit.evidence:
            yield visit.visit_id, ref
    for lt in core.laytime_by_cargo_port:
        for ref in lt.evidence:
            yield f"laytime {lt.visit_id}", ref
        for stop in lt.stoppages:
            for ref in stop.evidence:
                yield f"stoppage {lt.visit_id}", ref
    if core.latest_position is not None:
        for ref in core.latest_position.evidence:
            yield "latest_position", ref


def fill_quotes(
    refs: Iterable[tuple[str, EvidenceRef]], bodies: dict[str, str]
) -> None:
    """Copy each cited line range from the email body into `quote`."""
    for _, ref in refs:
        body = bodies.get(ref.source_id)
        ref.quote = cited_text(body, ref.first_line, ref.last_line) if body else None


def contract_emails(extractions: list[EmailExtraction]) -> list[EmailExtraction]:
    """Voyage emails classified as contract."""
    return [e for e in extractions if e.topics.get("contract") == "yes"]


def other_emails(extractions: list[EmailExtraction]) -> list[EmailExtraction]:
    """Voyage emails that are not in the contract set."""
    contract_ids = {e.source_id for e in contract_emails(extractions)}
    return [e for e in extractions if e.source_id not in contract_ids]


def user_pack(
    voyage: dict[str, Any],
    extractions: list[EmailExtraction],
    vessel: dict[str, Any] | None = None,
) -> str:
    """Itinerary plus the body of every email in this call."""
    itinerary = [
        {
            "visit_id": visit_id_for(item),
            "seq": item.get("seq"),
            "port": item.get("port"),
            "function": item.get("function"),
            "country": item.get("country"),
            "utc_offset": item.get("utc_offset"),
        }
        for item in voyage.get("itinerary", [])
    ]
    payload: dict[str, Any] = {
        "voyage_no": voyage.get("voyage_no"),
        "charterer": voyage.get("charterer"),
        "itinerary": itinerary,
        "emails": [
            {
                "source_id": e.source_id,
                "filename": e.filename,
                "subject": e.subject,
                "sent_at": e.sent_at,
                "vessel_name": e.vessel_name,
                "topics": e.topics,
                "body_lines": numbered_lines(email_body(e.filename)),
            }
            for e in extractions
        ],
    }
    if vessel:
        payload["vessel_name"] = vessel.get("name")
        payload["imo"] = vessel.get("imo")
    return json.dumps(payload, indent=2)


def laytime_pack(
    voyage: dict[str, Any],
    extractions: list[EmailExtraction],
    contract: ContractTerms,
    vessel: dict[str, Any] | None = None,
) -> str:
    """Non-contract emails plus the deduction rules already decided."""
    payload = json.loads(user_pack(voyage, extractions, vessel))
    payload["deduction_rules"] = [
        {"kind": rule.kind, "wording": rule.evidence.quote}
        for rule in contract.deduction_rules
    ]
    return json.dumps(payload, indent=2)


def _with_repair(pack: str, repair: str | None) -> str:
    if not repair:
        return pack
    return f"{pack}\n\n{repair}"


def resolve_contract(
    voyage: dict[str, Any],
    emails: list[EmailExtraction],
    *,
    vessel: dict[str, Any] | None = None,
    repair: str | None = None,
) -> ContractDraft:
    """One structured call on the contract emails. Missing when there are none."""
    if not emails or not llm.is_configured():
        return ContractDraft(contract=ContractTerms(status="missing"))
    return llm.generate(
        CONTRACT_PROMPT,
        _with_repair(user_pack(voyage, emails, vessel), repair),
        ContractDraft,
    )


def resolve_laytime(
    voyage: dict[str, Any],
    emails: list[EmailExtraction],
    contract: ContractTerms,
    *,
    vessel: dict[str, Any] | None = None,
    repair: str | None = None,
) -> LaytimeDraft | None:
    """One structured call on the other emails. None when there are none."""
    if not emails or not llm.is_configured():
        return None
    return llm.generate(
        LAYTIME_PROMPT,
        _with_repair(laytime_pack(voyage, emails, contract, vessel), repair),
        LaytimeDraft,
    )


def _overlay(skeleton: list, got: list, locked: tuple[str, ...]) -> list:
    """Keep itinerary identity; take the model's times and evidence when present."""
    found = {row.visit_id: row for row in got}
    merged = []
    for base in skeleton:
        row = found.get(base.visit_id)
        if row is None:
            merged.append(base)
            continue
        merged.append(
            row.model_copy(update={key: getattr(base, key) for key in locked})
        )
    return merged


def build_resolved_core(
    voyage: dict[str, Any],
    contract: ContractTerms,
    laytime: LaytimeDraft | None,
    questions: list[ResolverQuestion],
    notes: list[str],
) -> ResolvedVoyageCore:
    """Itinerary rows, with contract terms and any laytime the model returned."""
    skeleton = itinerary_skeleton(voyage)
    visits = skeleton.port_visits
    rows = skeleton.laytime_by_cargo_port
    latest_position = None
    if laytime is not None:
        visits = _overlay(
            skeleton.port_visits,
            laytime.port_visits,
            ("visit_id", "port", "function", "laytime_applies"),
        )
        rows = _overlay(
            skeleton.laytime_by_cargo_port,
            laytime.laytime_by_cargo_port,
            ("visit_id", "port"),
        )
        latest_position = laytime.latest_position
        questions = [*questions, *laytime.questions]
        notes = [*notes, *laytime.notes]
    return ResolvedVoyageCore(
        contract=contract,
        port_visits=visits,
        laytime_by_cargo_port=rows,
        latest_position=latest_position,
        questions=questions,
        notes=notes,
    )


def _assemble(
    voyage: dict[str, Any],
    extractions: list[EmailExtraction],
    vessel: dict[str, Any] | None,
    repair: str | None = None,
) -> ResolvedVoyageCore:
    bodies = bodies_for(extractions)
    contract_summary = resolve_contract(
        voyage, contract_emails(extractions), vessel=vessel, repair=repair
    )
    fill_quotes(contract_refs(contract_summary.contract), bodies)
    contract = finish_contract(contract_summary.contract, contract_summary.questions)
    laytime_summary = resolve_laytime(
        voyage,
        other_emails(extractions),
        contract,
        vessel=vessel,
        repair=repair,
    )
    core = build_resolved_core(
        voyage,
        contract,
        laytime_summary,
        contract_summary.questions,
        contract_summary.notes,
    )
    fill_quotes(evidence_refs(core), bodies)
    place_times(core, port_offsets(voyage))
    for lt in core.laytime_by_cargo_port:
        apply_rule_fractions(contract, lt.stoppages)
    return core


def resolve(
    voyage: dict[str, Any],
    extractions: list[EmailExtraction],
    *,
    vessel: dict[str, Any] | None = None,
) -> ResolvedVoyageCore:
    """Contract call, then laytime call. One retry if the output fails validation."""
    # Imported here: validate imports itinerary_skeleton from this module.
    from src.validate import validate_resolved

    core = _assemble(voyage, extractions, vessel)
    problems = validate_resolved(core, voyage, extractions, bodies_for(extractions))
    if not problems:
        return core
    repair = (
        "The previous answer was rejected. Fix these problems and return the "
        "full object again:\n" + "\n".join(f"- {problem}" for problem in problems)
    )
    return _assemble(voyage, extractions, vessel, repair)
