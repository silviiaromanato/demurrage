import json

import pytest

from demurrage import llm
from demurrage.answer import build_voyage_answer, render_brief
from demurrage.models import (
    ContractDraft,
    ContractTerms,
    DeductionRule,
    DemurrageAnswer,
    EvidenceRef,
    LatestPosition,
    LaytimeDraft,
    Stoppage,
    WrittenTime,
)
from demurrage.resolver import bodies_for, cited_text, itinerary_skeleton, resolve
from demurrage.reviews import review_for
from demurrage.validate import validate_answer, validate_brief, validate_resolved

SID = "email_01.eml:abc"
BODY = "agreed terms\n72 hours reversible\nPOSN 46-50N 006-10W"
BODIES = {SID: BODY}
VESSEL = {"name": "KESTREL BAY", "imo": "1"}


def ref(first: int, last: int | None = None, source_id: str = SID) -> EvidenceRef:
    last = first if last is None else last
    body = BODIES.get(source_id, "")
    return EvidenceRef(
        source_id=source_id,
        first_line=first,
        last_line=last,
        quote=cited_text(body, first, last),
    )


@pytest.fixture
def core(voyage):
    """Resolver output for the fixture voyage: 72 h reversible, USD 25,500/day."""
    skeleton = itinerary_skeleton(voyage)
    contract = ContractTerms(
        status="resolved",
        laytime_allowance_hours=72,
        reversible=True,
        turn_time_after_nor_hours=6,
        demurrage_rate_per_day=25500,
        currency="USD",
        evidence=[ref(2)],
    )
    events = {
        "pv-1": ("2026-03-01T00:00:00+00:00", "2026-03-02T20:00:00+00:00"),
        "pv-3": ("2026-03-19T18:00:00+00:00", "2026-03-23T23:30:00+00:00"),
    }
    laytime = [
        lt.model_copy(
            update={
                "nor_tendered": events[lt.visit_id][0],
                "final_hoses_disconnected": events[lt.visit_id][1],
                "status": "final",
            }
        )
        for lt in skeleton.laytime_by_cargo_port
    ]
    return skeleton.model_copy(
        update={"contract": contract, "laytime_by_cargo_port": laytime}
    )


def test_resolve_returns_itinerary_skeleton(voyage):
    core = itinerary_skeleton(voyage)
    assert [v.visit_id for v in core.port_visits] == ["pv-1", "pv-2", "pv-3"]
    assert [v.laytime_applies for v in core.port_visits] == [True, False, True]
    assert [lt.visit_id for lt in core.laytime_by_cargo_port] == ["pv-1", "pv-3"]
    assert core.contract.status == "missing"


def test_cited_text_is_inclusive_and_rejects_out_of_range():
    assert cited_text(BODY, 1, 2) == "agreed terms\n72 hours reversible"
    assert cited_text(BODY, 3, 3) == "POSN 46-50N 006-10W"
    assert cited_text(BODY, 3, 4) is None
    assert cited_text(BODY, 2, 1) is None


def test_voyage_answer_uses_resolver_position(core, voyage, fleet, make_extraction):
    position = LatestPosition(
        reported_at_written=None,
        reported_at="2026-02-03T12:00:00+00:00",
        description="46-50N 006-10W",
        evidence=[ref(3)],
    )
    core = core.model_copy(update={"latest_position": position})
    answer = build_voyage_answer(fleet, [make_extraction()], core, VESSEL, voyage)
    assert answer.latest_position == position


def test_end_to_end_fixture_numbers(core, voyage, fleet, make_extraction):
    extractions = [make_extraction()]
    assert validate_resolved(core, voyage, extractions, BODIES) == []
    answer = build_voyage_answer(fleet, extractions, core, VESSEL, voyage)
    used = [lt.used_duration_seconds / 3600 for lt in answer.laytime_by_cargo_port]
    assert used == [38.0, 95.5]
    assert answer.demurrage.excess_seconds / 3600 == 61.5
    assert answer.demurrage.amount_owed == 65343.75
    assert answer.voyage.status == "final"
    assert answer.emails[0].relevance == "relevant"
    assert validate_answer(answer, extractions) == []
    brief = render_brief(answer)
    assert f"`{SID}`" in brief
    assert "72 hours reversible" not in brief
    assert validate_brief(brief, answer) == []


def test_open_review_blocks_final_status(core, voyage, fleet, make_extraction):
    pending = make_extraction(voyage_about="uncertain")
    pending = pending.model_copy(update={"review": review_for(pending, fleet)})
    answer = build_voyage_answer(fleet, [pending], core, VESSEL, voyage)
    assert answer.voyage.status == "incomplete"
    assert answer.review_items[0].code == "VOYAGE_UNCERTAIN"
    assert validate_answer(answer, [pending]) == []


def _laytime_email(make_extraction):
    return make_extraction(
        source_id="email_19.eml:sof",
        filename="email_19.eml",
        content_hash="sof",
        topics={
            "contract": "no",
            "schedule": "yes",
            "laytime": "yes",
            "position": "no",
            "other": "no",
        },
    )


def test_resolve_calls_contract_then_laytime(monkeypatch, voyage, make_extraction):
    skeleton = itinerary_skeleton(voyage)
    calls: list = []
    contract_email = make_extraction()
    laytime_email = _laytime_email(make_extraction)
    shifting = EvidenceRef(
        source_id=contract_email.source_id, first_line=19, last_line=20
    )

    def generate(system, user, schema, name=None):
        calls.append((schema, system, user))
        if schema is ContractDraft:
            return ContractDraft(
                contract=ContractTerms(
                    laytime_allowance=3,
                    laytime_allowance_unit="days",
                    turn_time_after_nor_hours=6,
                    demurrage_rate_per_day=25500,
                    currency="USD",
                    deduction_rules=[
                        DeductionRule(
                            kind="shifting", fraction_deducted=1, evidence=shifting
                        )
                    ],
                    evidence=[
                        EvidenceRef(
                            source_id=contract_email.source_id,
                            first_line=13,
                            last_line=13,
                        )
                    ],
                ),
                notes=["recap"],
            )
        sof = laytime_email.source_id
        cotonou = skeleton.port_visits[2].model_copy(
            update={
                "arrival_written": WrittenTime(value="2026-02-15T16:20", basis="local"),
                "evidence": [EvidenceRef(source_id=sof, first_line=6, last_line=6)],
            }
        )
        laytime = skeleton.laytime_by_cargo_port[1].model_copy(
            update={
                "nor_tendered_written": WrittenTime(
                    value="2026-02-15T17:30", basis="local"
                ),
                "evidence": [EvidenceRef(source_id=sof, first_line=8, last_line=8)],
                "stoppages": [
                    Stoppage(
                        description="shifting",
                        cause="shifting",
                        deduction_rule="shifting",
                    ),
                    Stoppage(description="rain", cause="weather"),
                ],
            }
        )
        return LaytimeDraft(
            port_visits=[*skeleton.port_visits[:2], cotonou],
            laytime_by_cargo_port=[skeleton.laytime_by_cargo_port[0], laytime],
            latest_position=LatestPosition(
                reported_at_written=WrittenTime(
                    value="2026-02-15T16:20", basis="local"
                ),
                description="Cotonou",
                evidence=[
                    EvidenceRef(
                        source_id=laytime_email.source_id, first_line=6, last_line=6
                    )
                ],
            ),
            notes=["sof"],
        )

    monkeypatch.setenv(llm.API_KEY_ENV, "test")
    monkeypatch.setattr("demurrage.resolver.llm.generate", generate)
    core = resolve(voyage, [contract_email, laytime_email], vessel=VESSEL)
    assert [schema for schema, _, _ in calls] == [ContractDraft, LaytimeDraft]
    contract_system, contract_user = calls[0][1], calls[0][2]
    laytime_system, laytime_user = calls[1][1], calls[1][2]
    assert "addendum" in contract_system.lower()
    contract_payload = json.loads(contract_user)
    assert [e["source_id"] for e in contract_payload["emails"]] == [
        contract_email.source_id
    ]
    assert contract_payload["emails"][0]["body_lines"][12].startswith("13| LAYTIME")
    assert "or null if none applies" in laytime_system.lower()
    assert "counts in full" in laytime_system.lower()
    assert "do not deduct shifting" not in laytime_system.lower()
    assert "reported_at" in laytime_system
    assert "sent_at" in laytime_system
    assert "15/1630" in laytime_system
    assert "basis utc" in laytime_system.lower()
    laytime_payload = json.loads(laytime_user)
    assert [e["source_id"] for e in laytime_payload["emails"]] == [
        laytime_email.source_id
    ]
    assert "contract" not in laytime_payload
    [rule] = laytime_payload["deduction_rules"]
    assert rule["kind"] == "shifting"
    assert rule["wording"].startswith("- TIME USED SHIFTING FROM ANCHORAGE")
    assert "\n  AS LAYTIME." in rule["wording"]
    assert "KESTREL BAY" in laytime_user
    assert core.contract.status == "resolved"
    assert core.contract.laytime_allowance_hours == 72
    assert core.port_visits[2].arrival == "2026-02-15T16:20:00+01:00"
    assert core.laytime_by_cargo_port[1].nor_tendered == "2026-02-15T17:30:00+01:00"
    shifting_stop, rain = core.laytime_by_cargo_port[1].stoppages
    assert (shifting_stop.fraction_deducted, rain.fraction_deducted) == (1.0, 0.0)
    assert core.latest_position.reported_at is None
    assert core.contract.evidence[0].quote.startswith("LAYTIME       : 72 HOURS")
    assert core.notes == ["recap", "sof"]
    assert core.latest_position is not None
    assert core.latest_position.evidence[0].quote == "15/02/2026 16:20   EOSP"
    assert [v.visit_id for v in core.port_visits] == ["pv-1", "pv-2", "pv-3"]
    emails = [contract_email, laytime_email]
    assert validate_resolved(core, voyage, emails, bodies_for(emails)) == []


def test_resolve_retries_once_when_a_citation_is_out_of_range(
    monkeypatch, voyage, make_extraction
):
    skeleton = itinerary_skeleton(voyage)
    calls: list = []
    contract_email = make_extraction()

    def generate(system, user, schema, name=None):
        calls.append((schema, user))
        if schema is not ContractDraft:
            return LaytimeDraft(
                port_visits=skeleton.port_visits,
                laytime_by_cargo_port=skeleton.laytime_by_cargo_port,
            )
        line = 13 if "rejected" in user else 999
        return ContractDraft(
            contract=ContractTerms(
                laytime_allowance=72,
                laytime_allowance_unit="hours",
                turn_time_after_nor_hours=6,
                demurrage_rate_per_day=25500,
                currency="USD",
                evidence=[
                    EvidenceRef(
                        source_id=contract_email.source_id,
                        first_line=line,
                        last_line=line,
                    )
                ],
            )
        )

    monkeypatch.setenv(llm.API_KEY_ENV, "test")
    monkeypatch.setattr("demurrage.resolver.llm.generate", generate)
    core = resolve(voyage, [contract_email, _laytime_email(make_extraction)])
    assert [schema for schema, _ in calls] == [
        ContractDraft,
        LaytimeDraft,
        ContractDraft,
        LaytimeDraft,
    ]
    assert "lines 999-999 are outside" in calls[2][1]
    assert core.contract.evidence[0].first_line == 13


def test_resolve_without_model_returns_skeleton(monkeypatch, voyage):
    monkeypatch.delenv(llm.API_KEY_ENV, raising=False)
    monkeypatch.setenv(llm.CACHE_ENV, "off")

    def generate(*args, **kwargs):
        raise AssertionError("generate should not be called")

    monkeypatch.setattr("demurrage.resolver.llm.generate", generate)
    core = resolve(voyage, [])
    assert core == itinerary_skeleton(voyage)


def test_out_of_range_citation_is_rejected(core, voyage, make_extraction):
    bad = core.model_copy(
        update={
            "latest_position": LatestPosition(
                reported_at_written=None,
                description="46-50N 006-10W",
                evidence=[EvidenceRef(source_id=SID, first_line=4, last_line=9)],
            )
        }
    )
    problems = validate_resolved(bad, voyage, [make_extraction()], BODIES)
    assert any("latest_position: lines 4-9 are outside" in p for p in problems)


def test_tampered_quote_is_rejected(core, voyage, make_extraction):
    tampered = core.contract.evidence[0].model_copy(update={"quote": "96 hours"})
    bad = core.model_copy(
        update={"contract": core.contract.model_copy(update={"evidence": [tampered]})}
    )
    problems = validate_resolved(bad, voyage, [make_extraction()], BODIES)
    assert any("quote does not match" in p for p in problems)


def test_stoppage_rule_must_exist_and_match_its_fraction(core, voyage, make_extraction):
    contract = core.contract.model_copy(
        update={
            "deduction_rules": [
                DeductionRule(kind="shifting", fraction_deducted=1, evidence=ref(1))
            ]
        }
    )

    def with_stoppage(rule: str | None, fraction: float):
        stop = Stoppage(
            description="x",
            cause="shifting",
            fraction_deducted=fraction,
            deduction_rule=rule,
        )
        lt = core.laytime_by_cargo_port[0].model_copy(update={"stoppages": [stop]})
        return core.model_copy(
            update={
                "contract": contract,
                "laytime_by_cargo_port": [lt, core.laytime_by_cargo_port[1]],
            }
        )

    extractions = [make_extraction()]
    ok = with_stoppage("shifting", 1)
    assert validate_resolved(ok, voyage, extractions, BODIES) == []
    unknown = validate_resolved(with_stoppage("weather", 1), voyage, extractions)
    assert any("unknown rule 'weather'" in p for p in unknown)
    wrong = validate_resolved(with_stoppage("shifting", 0.5), voyage, extractions)
    assert any("0.5 does not follow rule 'shifting'" in p for p in wrong)
    unruled = validate_resolved(with_stoppage(None, 1), voyage, extractions)
    assert any("1.0 does not follow rule None" in p for p in unruled)


def test_resolver_must_not_fill_durations_or_cite_unknown_emails(
    core, voyage, make_extraction
):
    bad_lt = core.laytime_by_cargo_port[0].model_copy(
        update={"gross_duration_seconds": 1}
    )
    bad_contract = core.contract.model_copy(
        update={"evidence": [ref(1, source_id="missing.eml:0")]}
    )
    bad = core.model_copy(
        update={
            "contract": bad_contract,
            "laytime_by_cargo_port": [bad_lt, core.laytime_by_cargo_port[1]],
        }
    )
    problems = validate_resolved(bad, voyage, [make_extraction()])
    assert any("must not fill" in p for p in problems)
    assert any("unknown missing.eml:0" in p for p in problems)


def test_tampered_answer_fails_recompute(core, voyage, fleet, make_extraction):
    extractions = [make_extraction()]
    answer = build_voyage_answer(fleet, extractions, core, VESSEL, voyage)
    d = answer.demurrage.model_dump() | {"daily_rate": 30000, "amount_owed": 76875.0}
    tampered = answer.model_copy(update={"demurrage": DemurrageAnswer(**d)})
    assert "demurrage does not recompute" in " ".join(
        validate_answer(tampered, extractions)
    )
