"""Parse a .eml file into a ParsedEmail (Python only)."""

from __future__ import annotations

import hashlib
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from demurrage.models import ParsedEmail


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
