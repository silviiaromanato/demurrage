"""Classify one email with three Jev choice calls: voyage, vessel, topics."""

from __future__ import annotations

from typing import Any

import httpx

from src import llm
from src.models import (
    NOT_OUR_VESSEL,
    TOPICS,
    VESSEL_UNCERTAIN,
    EmailExtraction,
    ParsedEmail,
    TopicCheck,
    TopicFlag,
    VesselCheck,
    VoyageCheck,
)

VOYAGE_INSTRUCTIONS = (
    "Is this Halcyon Tankers email about a vessel voyage (its contract, "
    "port calls, cargo operations, laytime or position)?"
)
VOYAGE_CRITERIA = {
    "yes": "The email is about a voyage of a vessel.",
    "no": "The email is not about a vessel voyage.",
    "uncertain": "Cannot tell from this email whether it is about a voyage.",
}
VESSEL_INSTRUCTIONS = (
    "Which Halcyon fleet vessel is this email about? Choose uncertain if "
    "you cannot tell, or not_our_vessel if it is clearly another vessel."
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
        "laytime allowance and basis (e.g. SHINC, reversible), turn time "
        "after NOR, when laytime ends, demurrage rate and pro rata, despatch, "
        "deduction rules, exceptions while on demurrage. "
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
        "(a position report)?"
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
    criteria[VESSEL_UNCERTAIN] = (
        "Cannot tell which Halcyon fleet vessel this email is about, or the "
        "identification is ambiguous."
    )
    criteria[NOT_OUR_VESSEL] = (
        "The email is clearly about a vessel that is not in the Halcyon fleet "
        "(another owner or name not in the fleet list)."
    )
    return criteria


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
        VOYAGE_INSTRUCTIONS,
        VOYAGE_CRITERIA,
    )
    return VoyageCheck.from_jev(answer)


def check_vessel(parsed: ParsedEmail, fleet: dict[str, Any]) -> VesselCheck:
    answer = llm.choose(
        email_state(parsed),
        "vessel",
        VESSEL_INSTRUCTIONS,
        vessel_criteria(fleet),
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
            return EmailExtraction(
                **metadata,
                voyage_about="no",
                topics=dict.fromkeys(TOPICS, "no"),
                warnings=[f"not about a voyage (Jev confidence {voyage.confidence})"],
                extraction_status="complete",
            )
        vessel = check_vessel(parsed, fleet)
        if vessel.choice == NOT_OUR_VESSEL:
            return EmailExtraction(
                **metadata,
                voyage_about="no",
                topics=dict.fromkeys(TOPICS, "no"),
                warnings=["vessel is not in the Halcyon fleet (Jev)"],
                extraction_status="complete",
            )
        topics = check_topics(parsed)
        return EmailExtraction(
            **metadata,
            vessel_name=None if vessel.choice == VESSEL_UNCERTAIN else vessel.choice,
            voyage_about=voyage.choice,
            topics=topics.model_dump(exclude={"confidence"}),
            extraction_status="complete",
        )
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
        return EmailExtraction(
            **metadata,
            warnings=[f"extraction_status=pending: Jev failed: {exc}"],
        )
