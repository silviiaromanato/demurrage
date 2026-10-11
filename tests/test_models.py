import pytest
from pydantic import ValidationError

from demurrage.models import (
    CargoPortLaytime,
    ContractTerms,
    DemurrageAnswer,
    EmailReview,
    EvidenceRef,
    ResolvedVoyageCore,
    Stoppage,
)


@pytest.mark.parametrize(
    "build",
    [
        lambda: CargoPortLaytime(
            visit_id="pv-1",
            port="A",
            gross_duration_seconds=10,
            deducted_duration_seconds=2,
            used_duration_seconds=9,
        ),
        lambda: DemurrageAnswer(
            total_used_seconds=100, allowed_seconds=50, excess_seconds=40
        ),
        lambda: EmailReview(
            code="X", prompt="?", options=["a"], status="answered", answer="b"
        ),
        lambda: ContractTerms(status="resolved"),
        lambda: Stoppage(
            description="pump",
            cause="vessel",
            start="2026-03-01T12:00:00+00:00",
            end="2026-03-01T11:00:00+01:00",
        ),
    ],
    ids=[
        "used-mismatch",
        "excess-mismatch",
        "bad-answer",
        "resolved-empty",
        "reversed",
    ],
)
def test_models_reject_inconsistent_data(build):
    with pytest.raises(ValidationError):
        build()


def test_stoppage_compares_times_across_offsets():
    stop = Stoppage(
        description="pump",
        cause="vessel",
        start="2026-03-01T12:00:00+01:00",
        end="2026-03-01T11:30:00+00:00",
    )
    assert stop.end


def _properties(schema: dict, name: str) -> set[str]:
    return set(schema["$defs"][name]["properties"])


def test_llm_schema_hides_python_filled_fields():
    schema = ResolvedVoyageCore.model_json_schema()
    laytime = _properties(schema, "CargoPortLaytime")
    assert "nor_tendered" in laytime
    assert not laytime & {
        "laytime_start",
        "gross_duration_seconds",
        "deducted_duration_seconds",
        "used_duration_seconds",
    }
    assert _properties(schema, "EvidenceRef") == {"source_id", "quote"}


def test_hidden_fields_still_load_from_result_json():
    ref = EvidenceRef.model_validate(
        {"source_id": "s", "quote": "q", "start_offset": 1, "end_offset": 2}
    )
    assert (ref.start_offset, ref.end_offset) == (1, 2)
