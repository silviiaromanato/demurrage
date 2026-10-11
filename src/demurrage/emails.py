"""Per-email work: parse a .eml file, then classify and extract facts from it."""

from __future__ import annotations

import hashlib
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from demurrage import llm
from demurrage.models import EmailExtraction, EmailReview, ParsedEmail

NOT_OUR_VESSEL = "not_our_vessel"


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


EXTRACTION_SYSTEM = (
    "Classify one shipping email and extract typed facts with exact quotes. "
    "Do not calculate durations, choose between emails, or invent values."
)


def extract(parsed: ParsedEmail) -> EmailExtraction:
    """One LLM call per email. Metadata always comes from the parser, not the model."""
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
            warnings=[
                "extraction_status=pending: LLM extraction not configured; "
                "facts lists are empty."
            ],
        )
    user = (
        f"source_id: {parsed.source_id}\n"
        f"subject: {parsed.subject}\n\n{parsed.body_text}"
    )
    result = llm.structured_call(EXTRACTION_SYSTEM, user, EmailExtraction)
    return result.model_copy(update={**metadata, "extraction_status": "complete"})


def review_for(e: EmailExtraction, fleet_vessels: list[str]) -> EmailReview | None:
    """Python check: does this extraction need a human answer before continuing?"""
    if e.extraction_status != "complete" or e.relevance == "not_relevant":
        return None
    if e.voyage_about == "uncertain" and e.vessel_name is None:
        return EmailReview(
            code="VESSEL_UNCERTAIN",
            prompt=f"Which vessel is {e.filename} about?",
            options=[*fleet_vessels, NOT_OUR_VESSEL],
        )
    return None
