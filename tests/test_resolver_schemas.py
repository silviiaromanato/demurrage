import pytest
from pydantic import ValidationError

from demurrage.resolver_facts import ExtractedFact, PortEventFact
from demurrage.resolver_schemas import EvidenceQuote


def test_evidence_quote_drops_offsets():
    quote = EvidenceQuote(source_id="email_01.eml:abc", quote="72 hours")
    ref = quote.to_evidence_ref()
    assert ref.source_id == "email_01.eml:abc"
    assert ref.quote == "72 hours"
    assert ref.start_offset is None
    assert ref.end_offset is None


def test_fact_id_must_match_source():
    fact = ExtractedFact(
        fact_id="email_01.eml:abc#1",
        fact_type="rate",
        value=25500,
        status="final",
        quote="USD 25,500",
        source_id="email_01.eml:abc",
    )
    assert fact.fact_id.endswith("#1")
    with pytest.raises(ValidationError):
        ExtractedFact(
            fact_id="other#1",
            fact_type="rate",
            value=1,
            status="final",
            quote="q",
            source_id="email_01.eml:abc",
        )


def test_port_event_fact_keeps_event_fields():
    fact = PortEventFact(
        fact_id="email_03.eml:abc#nor",
        fact_type="event",
        value="2026-01-30T10:00:00+01:00",
        status="actual",
        quote="NOR tendered",
        source_id="email_03.eml:abc",
        event_type="nor_tendered",
        port="Antwerp",
        is_actual=True,
    )
    assert fact.event_type == "nor_tendered"
    assert fact.port == "Antwerp"
