import pytest

from demurrage.models import EmailExtraction

VOYAGE = {
    "voyage_no": "KB-2603",
    "itinerary": [
        {"seq": 1, "port": "Antwerp", "function": "load", "utc_offset": "+01:00"},
        {
            "seq": 2,
            "port": "Gibraltar",
            "function": "bunkering",
            "utc_offset": "+01:00",
        },
        {
            "seq": 3,
            "port": "Cotonou",
            "function": "discharge",
            "utc_offset": "+01:00",
        },
    ],
}


@pytest.fixture
def voyage() -> dict:
    return VOYAGE


@pytest.fixture
def fleet() -> dict:
    """KESTREL BAY on the active voyage; KESTREL POINT in the fleet but idle."""
    return {
        "vessels": [
            {"name": "KESTREL BAY", "imo": "1", "current_voyage": VOYAGE},
            {"name": "KESTREL POINT", "imo": "2"},
        ]
    }


@pytest.fixture
def make_extraction():
    """A complete, relevant contract email for KESTREL BAY; override any field."""

    def make(**update) -> EmailExtraction:
        base = {
            "source_id": "email_01.eml:abc",
            "filename": "email_01.eml",
            "content_hash": "abc",
            "message_id": None,
            "subject": "CP",
            "sent_at": None,
            "extraction_status": "complete",
            "voyage_about": "yes",
            "vessel_name": "KESTREL BAY",
            "topics": {
                "contract": "yes",
                "schedule": "no",
                "laytime": "no",
                "position": "no",
                "other": "no",
            },
        }
        return EmailExtraction(**(base | update))

    return make
