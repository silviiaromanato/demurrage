"""Per-email work: parse a .eml file, then classify and extract facts from it."""

from __future__ import annotations

import hashlib
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from demurrage import llm
from demurrage.answer import finalize_extraction
from demurrage.models import (
    NOT_OUR_VESSEL,
    TOPICS,
    UNKNOWN_VESSEL,
    VESSEL_UNCERTAIN,
    EmailExtraction,
    EmailReview,
    ParsedEmail,
    TopicCheck,
    TopicFlag,
    VesselCheck,
    VoyageCheck,
)


def _addresses(header_value: str | None) -> list[str]:
    if not header_value:
        return []
    return [addr for _, addr in getaddresses([header_value]) if addr]


def source_id_for(path: Path) -> str:
    return f"{path.name}:{hashlib.sha256(path.read_bytes()).hexdigest()[:16]}"


def parse_eml_file(path: Path) -> ParsedEmail:
    raw = path.read_bytes()
    content_hash = hashlib.sha256(raw).hexdigest()
    message = BytesParser(policy=policy.default).parsebytes(raw)
    warnings: list[str] = []

    sent_at: str | None = None
    if date_header := message.get("Date"):
        try:
            sent_at = parsedate_to_datetime(date_header).isoformat()
        except (TypeError, ValueError, OSError):
            warnings.append("could not parse Date header")

    body_text = ""
    body_part = message.get_body(preferencelist=("plain",))
    if body_part is None:
        warnings.append("no plain-text body found")
    elif isinstance(content := body_part.get_content(), str):
        body_text = content
    else:
        warnings.append("plain-text body could not be decoded")

    return ParsedEmail(
        source_id=source_id_for(path),
        filename=path.name,
        content_hash=content_hash,
        message_id=message.get("Message-ID"),
        in_reply_to=message.get("In-Reply-To"),
        references=(message.get("References") or "").split(),
        from_address=message.get("From"),
        to_addresses=_addresses(message.get("To")),
        cc_addresses=_addresses(message.get("Cc")),
        subject=message.get("Subject"),
        sent_at=sent_at,
        body_text=body_text,
        parse_warnings=warnings,
    )


TOPIC_CRITERIA = {
    "yes": "The email discusses this topic.",
    "no": "The email does not discuss this topic.",
    "unknown": "Cannot tell from this email.",
}

TOPIC_INSTRUCTIONS = {
    "contract": (
        "Does this email state or amend the agreed charter-party terms themselves "
        "(a recap or addendum)? That includes anything the contract defines: "
        "vessel, charterer, cargo, laycan, contracted load and discharge ports, "
        "freight, laytime allowance and basis (e.g. SHINC, reversible), turn time "
        "after NOR, when laytime ends, demurrage rate and pro rata, despatch, "
        "deduction rules, exceptions while on demurrage, claim time bar. "
        "Do not count reports of events that later happened under those terms."
    ),
    "schedule": (
        "Does this email report a real or estimated schedule event or update: "
        "an ETA/ETD/ETC revision, arrival (EOSP, anchored), berthing, sailing, "
        "or a port being added, dropped or reordered? These are things that "
        "happened or are forecast, not the contracted itinerary in a recap."
    ),
    "laytime": (
        "Does this email report a real or estimated event that starts, stops or "
        "interrupts the laytime clock at a load or discharge port: NOR tendered, "
        "hoses connected or disconnected, cargo operations started or finished, "
        "a statement of facts, or a stoppage and its cause? Not the contract "
        "rule that says how those events should count."
    ),
    "position": (
        "Does this email report where the vessel is, was, or is expected to be "
        "(noon or position report, coordinates, speed, distance to go)?"
    ),
    "other": (
        "Does this email report some other real-world event or update that is "
        "not contract terms, schedule, laytime or position (disbursement "
        "account, bunkers/BDN taken, bill of lading issued, crew, admin)?"
    ),
}


def email_state(parsed: ParsedEmail) -> dict[str, Any]:
    return {
        "from": parsed.from_address,
        "to": parsed.to_addresses,
        "date": parsed.sent_at,
        "subject": parsed.subject,
        "body": parsed.body_text,
    }


def vessel_criteria(fleet: dict[str, Any]) -> dict[str, str]:
    criteria = {}
    for v in fleet.get("vessels", []):
        voyage = (v.get("current_voyage") or {}).get("voyage_no") or "none"
        criteria[v["name"]] = (
            f"{v['name']}, IMO {v.get('imo')}, call sign {v.get('call_sign')}, "
            f"status {v.get('status')}, current voyage {voyage}"
        )
    uncertain = (
        "Cannot tell which Halcyon fleet vessel this email is about, or the "
        "identification is ambiguous."
    )
    criteria[VESSEL_UNCERTAIN] = uncertain
    criteria[UNKNOWN_VESSEL] = uncertain  # legacy Jev label; same as uncertain
    criteria[NOT_OUR_VESSEL] = (
        "The email is clearly about a vessel that is not in the Halcyon fleet "
        "(another owner or name not in the fleet list)."
    )
    return criteria


def _vessel_choice_needs_review(choice: str) -> bool:
    return choice in (UNKNOWN_VESSEL, VESSEL_UNCERTAIN)


def _extraction_after_vessel(
    vessel_choice: str,
    voyage_about: str,
    metadata: dict[str, Any],
    flags: dict[str, str],
) -> EmailExtraction:
    if vessel_choice == NOT_OUR_VESSEL:
        return EmailExtraction(
            **metadata,
            voyage_about="no",
            vessel_name=None,
            topics=dict.fromkeys(TOPICS, "no"),
            warnings=["vessel is not in the Halcyon fleet (Jev)"],
            extraction_status="complete",
        )
    return EmailExtraction(
        **metadata,
        vessel_name=(
            None if _vessel_choice_needs_review(vessel_choice) else vessel_choice
        ),
        voyage_about=voyage_about,
        topics=flags,
        extraction_status="complete",
    )


def _topic_flag(choice: str) -> TopicFlag:
    if choice == "unknown":
        return "uncertain"
    if choice in ("yes", "no", "uncertain"):
        return choice
    raise ValueError(f"unexpected topic choice {choice!r}")


def check_voyage(parsed: ParsedEmail) -> VoyageCheck:
    answer = llm.choose(
        email_state(parsed),
        "voyage",
        "Is this Halcyon Tankers email about a vessel voyage (its contract, "
        "port calls, cargo operations, laytime or position)?",
        {
            "yes": "The email is about a voyage of a vessel.",
            "no": "The email is not about a vessel voyage.",
            "uncertain": "Cannot tell from this email whether it is about a voyage.",
        },
    )
    return VoyageCheck.from_jev(answer)


def check_vessel(parsed: ParsedEmail, fleet: dict[str, Any]) -> VesselCheck:
    criteria = vessel_criteria(fleet)
    answer = llm.choose(
        email_state(parsed),
        "vessel",
        "Which Halcyon fleet vessel is this email about? Choose uncertain if "
        "you cannot tell, or not_our_vessel if it is clearly another vessel.",
        criteria,
    )
    return VesselCheck.from_jev(answer)


def check_topics(parsed: ParsedEmail) -> TopicCheck:
    answers = llm.decide(
        email_state(parsed),
        {
            topic: llm.choice_question(TOPIC_INSTRUCTIONS[topic], TOPIC_CRITERIA)
            for topic in TOPICS
        },
    )
    return TopicCheck(
        **{topic: _topic_flag(answers[topic]["choice"]) for topic in TOPICS},
        confidence={
            topic: answers[topic].get("confidence")
            for topic in TOPICS
            if answers[topic].get("confidence") is not None
        },
    )


def _prior_sort_key(review: EmailReview) -> tuple[int, int | str]:
    if review.code == "VOYAGE_UNCERTAIN":
        return (0, 0)
    if review.code == "VESSEL_UNCERTAIN":
        return (1, 0)
    if review.code.startswith("TOPIC_"):
        topic = review.code.removeprefix("TOPIC_")
        try:
            return (2, TOPICS.index(topic))
        except ValueError:
            return (2, 99)
    return (3, review.code)


def review_applicable(e: EmailExtraction, review: EmailReview) -> bool:
    if review.code == "VOYAGE_UNCERTAIN":
        return e.voyage_about == "uncertain"
    if review.code == "VESSEL_UNCERTAIN":
        return e.voyage_about == "yes" and e.vessel_name is None
    if review.code.startswith("TOPIC_"):
        topic = review.code.removeprefix("TOPIC_")
        return e.topics.get(topic) == "uncertain"
    return False


def replay_reviews(
    e: EmailExtraction, priors: list[EmailReview], fleet: dict[str, Any]
) -> EmailExtraction:
    for prior in sorted(priors, key=_prior_sort_key):
        if prior.status != "answered" or prior.answer is None:
            continue
        if review_applicable(e, prior):
            e = finalize_extraction(apply_review(e, prior, fleet), fleet)
    return e


def advance_extraction(
    e: EmailExtraction,
    answered: EmailReview,
    fleet: dict[str, Any],
    answered_reviews: list[EmailReview],
) -> EmailExtraction:
    e = finalize_extraction(apply_review(e, answered, fleet), fleet)
    return e.model_copy(
        update={
            "answered_reviews": answered_reviews,
            "review": review_for(e, fleet),
        }
    )


def extract(parsed: ParsedEmail, fleet: dict[str, Any]) -> EmailExtraction:
    """Three Jev calls per email; metadata always comes from the parser."""
    metadata = {
        "source_id": parsed.source_id,
        "filename": parsed.filename,
        "content_hash": parsed.content_hash,
        "message_id": parsed.message_id,
        "subject": parsed.subject,
        "sent_at": parsed.sent_at,
    }
    if not llm.is_configured():
        return EmailExtraction(
            **metadata,
            warnings=["extraction_status=pending: LLM extraction not configured."],
        )
    try:
        voyage = check_voyage(parsed)
        if voyage.choice == "no":
            return finalize_extraction(
                EmailExtraction(
                    **metadata,
                    voyage_about="no",
                    topics=dict.fromkeys(TOPICS, "no"),
                    warnings=[
                        f"not about a voyage (Jev confidence {voyage.confidence})"
                    ],
                    extraction_status="complete",
                ),
                fleet,
            )
        vessel = check_vessel(parsed, fleet)
        topics = check_topics(parsed)
        flags = topics.model_dump(exclude={"confidence"})
        extraction = _extraction_after_vessel(
            vessel.choice, voyage.choice, metadata, flags
        )
        return finalize_extraction(extraction, fleet)
    except (httpx.HTTPError, ValueError, KeyError, llm.JevCacheMissError) as exc:
        return EmailExtraction(
            **metadata,
            warnings=[f"extraction_status=pending: Jev failed: {exc}"],
        )


def apply_review(
    e: EmailExtraction, review: EmailReview, fleet: dict[str, Any]
) -> EmailExtraction:
    """Write a human answer onto the extraction fields."""
    if review.status != "answered" or review.answer is None:
        raise ValueError("can only apply an answered review")
    updates: dict[str, Any] = {}
    if review.code == "VOYAGE_UNCERTAIN":
        updates["voyage_about"] = review.answer
        if review.answer == "no":
            updates["topics"] = dict.fromkeys(TOPICS, "no")
    elif review.code == "VESSEL_UNCERTAIN":
        if review.answer == NOT_OUR_VESSEL:
            updates["voyage_about"] = "no"
            updates["vessel_name"] = None
            updates["topics"] = dict.fromkeys(TOPICS, "no")
        else:
            updates["vessel_name"] = review.answer
    elif review.code.startswith("TOPIC_"):
        topic = review.code.removeprefix("TOPIC_")
        updates["topics"] = {**e.topics, topic: review.answer}
    else:
        raise ValueError(f"unknown review code {review.code}")
    return e.model_copy(update=updates)


def review_for(e: EmailExtraction, fleet: dict[str, Any]) -> EmailReview | None:
    """Next question: voyage?, vessel, then topics."""
    if e.extraction_status != "complete" or e.voyage_about == "no":
        return None
    vessels = [v["name"] for v in fleet.get("vessels", [])]
    if e.voyage_about == "uncertain":
        return EmailReview(
            code="VOYAGE_UNCERTAIN",
            prompt=f"Is {e.filename} about a vessel voyage?",
            options=["yes", "no"],
        )
    if e.vessel_name is None:
        return EmailReview(
            code="VESSEL_UNCERTAIN",
            prompt=f"Which vessel is {e.filename} about?",
            options=[*vessels, NOT_OUR_VESSEL],
        )
    for topic in TOPICS:
        if e.topics[topic] == "uncertain":
            return EmailReview(
                code=f"TOPIC_{topic}",
                prompt=f"Does {e.filename} discuss {topic}?",
                options=["yes", "no"],
            )
    return None
