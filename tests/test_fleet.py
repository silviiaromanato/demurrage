import pytest

from src.fleet import (
    UnknownVoyage,
    emails_for_voyage_no,
    extractions_for_active_voyage,
    find_active_voyage,
    find_voyage,
    relevance,
)


def test_find_active_voyage(fleet):
    vessel, voyage = find_active_voyage(fleet)
    assert vessel["name"] == "KESTREL BAY"
    assert voyage["voyage_no"] == "KB-2603"
    assert find_active_voyage({"vessels": [{"name": "X"}]}) is None


def test_find_voyage_by_number_or_vessel_name(fleet):
    by_number = find_voyage(fleet, "kb-2603")
    by_name = find_voyage(fleet, "  kestrel bay ")
    assert by_number == by_name
    assert by_number[0]["name"] == "KESTREL BAY"
    assert by_number[1]["voyage_no"] == "KB-2603"


def test_voyage_number_wins_over_a_vessel_of_the_same_name():
    fleet = {
        "vessels": [
            {
                "name": "KB-2603",
                "current_voyage": {"voyage_no": "OTHER"},
            },
            {
                "name": "KESTREL BAY",
                "current_voyage": {"voyage_no": "KB-2603"},
            },
        ]
    }
    vessel, voyage = find_voyage(fleet, "KB-2603")
    assert vessel["name"] == "KESTREL BAY"
    assert voyage["voyage_no"] == "KB-2603"


def test_find_voyage_rejects_unknown_and_idle_vessels(fleet):
    with pytest.raises(UnknownVoyage, match="matches 'NO SUCH'"):
        find_voyage(fleet, "NO SUCH")
    with pytest.raises(UnknownVoyage, match="no current voyage"):
        find_voyage(fleet, "KESTREL POINT")
    with pytest.raises(UnknownVoyage, match="Pass a voyage number"):
        find_voyage(fleet, "  ")


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
    assert emails_for_voyage_no([ours, other, pending], fleet, "KB-2603") == [ours]
    assert emails_for_voyage_no([ours], fleet, "OTHER") == []
