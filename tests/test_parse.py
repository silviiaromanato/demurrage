from pathlib import Path

from demurrage.parse import parse_eml_file, source_id_for

EMAILS = sorted(Path("emails").glob("*.eml"))


def test_all_emails_parse_with_a_body():
    assert len(EMAILS) == 19
    for path in EMAILS:
        parsed = parse_eml_file(path)
        assert parsed.body_text.strip(), path.name
        assert parsed.sent_at is not None, path.name
        assert parsed.parse_warnings == [], path.name


def test_source_ids_are_stable_and_unique():
    ids = [source_id_for(path) for path in EMAILS]
    assert ids == [source_id_for(path) for path in EMAILS]
    assert len(set(ids)) == len(ids)
    for sid, path in zip(ids, EMAILS, strict=True):
        assert sid.startswith(f"{path.name}:")


def test_headers_are_split_into_fields():
    parsed = parse_eml_file(Path("emails/email_01.eml"))
    assert parsed.subject.startswith("KESTREL BAY")
    assert parsed.message_id == "<recap-kb-0126@meridian-ship.example>"
    assert parsed.sent_at == "2026-01-26T17:42:10+00:00"
    assert parsed.to_addresses
