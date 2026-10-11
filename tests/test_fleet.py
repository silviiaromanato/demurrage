import pytest

from demurrage.fleet import extractions_for_active_voyage, find_active_voyage, relevance


def test_find_active_voyage(fleet):
    vessel, voyage = find_active_voyage(fleet)
    assert vessel["name"] == "KESTREL BAY"
    assert voyage["voyage_no"] == "KB-2603"
    assert find_active_voyage({"vessels": [{"name": "X"}]}) is None


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({}, "relevant"),
        ({"vessel_name": "KESTREL POINT"}, "not_relevant"),
        ({"vessel_name": "MT STRANGER"}, "uncertain"),
        ({"vessel_name": None}, "uncertain"),
        ({"voyage_about": "no"}, "not_relevant"),
        ({"voyage_about": "uncertain"}, "uncertain"),
        ({"extraction_status": "pending"}, "uncertain"),
    ],
)
def test_relevance(fleet, make_extraction, update, expected):
    assert relevance(make_extraction(**update), fleet) == expected


def test_no_active_voyage_means_uncertain(make_extraction):
    assert relevance(make_extraction(), {"vessels": []}) == "uncertain"


def test_active_voyage_keeps_only_relevant_emails(fleet, make_extraction):
    ours = make_extraction()
    other = make_extraction(source_id="email_04.eml:x", vessel_name="KESTREL POINT")
    pending = make_extraction(source_id="email_05.eml:y", extraction_status="pending")
    kept = extractions_for_active_voyage([ours, other, pending], fleet)
    assert kept == [ours]
