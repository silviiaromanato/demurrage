"""Pydantic schemas for every pipeline stage.

Each model validates its own internal consistency on construction and on load
from JSON. Checks that need data from another stage live in `validate.py`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Relevance = Literal["relevant", "not_relevant", "uncertain"]
AnswerStatus = Literal["final", "provisional", "incomplete"]
PortVisitStatus = Literal["final", "provisional", "missing", "conflicted"]
FactStatus = Literal["actual", "estimated", "interim", "final", "corrected", "unknown"]
Seconds = int | None

TOPICS = ("contract", "schedule", "laytime", "position", "other")
MONEY_TOLERANCE = 0.01


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Stage 1: parse (Python) ---


class ParsedEmail(Schema):
    source_id: str
    filename: str
    content_hash: str
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    from_address: str | None
    to_addresses: list[str]
    cc_addresses: list[str]
    subject: str | None
    sent_at: str | None
    body_text: str
    parse_warnings: list[str] = []


# --- Stage 2: extract (LLM, one call per email) ---


class ExtractedFact(Schema):
    fact_id: str
    fact_type: str
    value: Any
    unit: str | None = None
    timezone_label: str | None = None
    status: FactStatus
    quote: str = Field(min_length=1)
    source_id: str

    @model_validator(mode="after")
    def _fact_id_belongs_to_source(self) -> ExtractedFact:
        if not self.fact_id.startswith(f"{self.source_id}#"):
            raise ValueError(f"fact_id {self.fact_id!r} must start with source_id#")
        return self


class PortEventFact(ExtractedFact):
    event_type: str
    port: str | None = None
    raw_time_expression: str | None = None
    is_actual: bool | None = None


class EmailReview(Schema):
    """A question for a human; while open, ingest of further emails stops."""

    code: str
    prompt: str
    options: list[str] = Field(min_length=1)
    status: Literal["open", "answered"] = "open"
    answer: str | None = None
    answered_at: str | None = None

    @model_validator(mode="after")
    def _answer_matches_status(self) -> EmailReview:
        if self.status == "answered":
            if self.answer not in self.options or not self.answered_at:
                raise ValueError("answered review needs an allowed answer and time")
        elif self.answer is not None:
            raise ValueError("open review must not carry an answer")
        return self


class EmailExtraction(Schema):
    source_id: str
    filename: str
    content_hash: str
    message_id: str | None
    subject: str | None
    sent_at: str | None
    vessel_name: str | None = None
    voyage_no: str | None = None
    voyage_about: Literal["yes", "no", "uncertain"] = "uncertain"
    relevance: Relevance = "uncertain"
    affects_schedule: bool = False
    affects_laytime: bool = False
    topics: dict[str, bool] = Field(
        default_factory=lambda: dict.fromkeys(TOPICS, False)
    )
    contract_facts: list[ExtractedFact] = []
    port_events: list[PortEventFact] = []
    stoppages: list[ExtractedFact] = []
    position_facts: list[ExtractedFact] = []
    warnings: list[str] = []
    extraction_status: Literal["pending", "complete"] = "pending"
    review: EmailReview | None = None

    def all_facts(self) -> list[ExtractedFact]:
        return [
            *self.contract_facts,
            *self.port_events,
            *self.stoppages,
            *self.position_facts,
        ]

    @model_validator(mode="after")
    def _facts_are_from_this_email(self) -> EmailExtraction:
        ids = [f.fact_id for f in self.all_facts()]
        if any(f.source_id != self.source_id for f in self.all_facts()):
            raise ValueError("every fact must carry this email's source_id")
        if len(ids) != len(set(ids)):
            raise ValueError("fact_ids must be unique within an email")
        if set(self.topics) != set(TOPICS):
            raise ValueError(f"topics must have exactly the keys {TOPICS}")
        return self


# --- Stage 3: resolve (LLM, one call per voyage) and the public answer ---


class EvidenceRef(Schema):
    source_id: str
    quote: str = Field(min_length=1)
    start_offset: int | None = None
    end_offset: int | None = None
    note: str | None = None


class VoyageSummary(Schema):
    voyage_id: str
    vessel_name: str
    imo: str
    charterer: str | None
    status: AnswerStatus


class EmailAssessment(Schema):
    source_id: str
    subject: str | None
    vessel_name: str | None
    voyage_no: str | None
    relevance: Relevance
    affects_schedule: bool
    affects_laytime: bool
    topics: dict[str, bool]
    reason: str


class ContractTerms(Schema):
    status: Literal["resolved", "missing", "conflicted", "provisional"]
    laytime_allowance_hours: float | None = Field(default=None, gt=0)
    reversible: bool | None = None
    turn_time_after_nor_hours: float | None = Field(default=None, ge=0)
    laytime_end_event: str | None = None
    demurrage_rate_per_day: float | None = Field(default=None, gt=0)
    currency: str | None = None
    pro_rata: bool | None = None
    deduction_rules: list[str] = []
    exceptions_continue_on_demurrage: bool | None = None
    amendments: list[dict[str, Any]] = []
    evidence: list[EvidenceRef] = []

    @model_validator(mode="after")
    def _resolved_has_calculator_inputs(self) -> ContractTerms:
        if self.status == "resolved":
            required = {
                "laytime_allowance_hours": self.laytime_allowance_hours,
                "turn_time_after_nor_hours": self.turn_time_after_nor_hours,
                "demurrage_rate_per_day": self.demurrage_rate_per_day,
                "currency": self.currency,
            }
            if missing := [k for k, v in required.items() if v is None]:
                raise ValueError(f"resolved contract is missing {missing}")
            if not self.evidence:
                raise ValueError("resolved contract needs evidence")
        return self


class PortVisit(Schema):
    """One port on the voyage (load, discharge, bunkering, ...)."""

    visit_id: str
    port: str
    function: str
    arrival: str | None = None
    departure: str | None = None
    laytime_applies: bool = False
    status: PortVisitStatus = "missing"
    evidence: list[EvidenceRef] = []


class StoppageDeduction(Schema):
    description: str
    duration_seconds: Seconds = Field(default=None, ge=0)
    contract_rule: str | None = None
    evidence: list[EvidenceRef] = []


class CargoPortLaytime(Schema):
    visit_id: str
    port: str
    nor_tendered: str | None = None
    laytime_start: str | None = None
    final_hoses_disconnected: str | None = None
    gross_duration_seconds: Seconds = Field(default=None, ge=0)
    stoppages: list[StoppageDeduction] = []
    deducted_duration_seconds: Seconds = Field(default=None, ge=0)
    used_duration_seconds: Seconds = Field(default=None, ge=0)
    calculation_notes: list[str] = []
    status: PortVisitStatus = "missing"
    evidence: list[EvidenceRef] = []

    @model_validator(mode="after")
    def _used_is_gross_minus_deducted(self) -> CargoPortLaytime:
        g, d, u = (
            self.gross_duration_seconds,
            self.deducted_duration_seconds,
            self.used_duration_seconds,
        )
        if None not in (g, d, u) and u != g - d:
            raise ValueError(f"{self.visit_id}: used {u} != gross {g} - deducted {d}")
        return self


class LatestPosition(Schema):
    reported_at: str | None
    description: str | None
    evidence: list[EvidenceRef] = []


class DemurrageAnswer(Schema):
    aggregation_method: str | None = None
    total_used_seconds: Seconds = Field(default=None, ge=0)
    allowed_seconds: Seconds = Field(default=None, ge=0)
    excess_seconds: Seconds = Field(default=None, ge=0)
    daily_rate: float | None = Field(default=None, gt=0)
    currency: str | None = None
    amount_owed: float | None = Field(default=None, ge=0)
    calculation_formula: str | None = None
    status: AnswerStatus = "incomplete"
    evidence: list[EvidenceRef] = []

    @model_validator(mode="after")
    def _arithmetic_is_consistent(self) -> DemurrageAnswer:
        used, allowed, excess = (
            self.total_used_seconds,
            self.allowed_seconds,
            self.excess_seconds,
        )
        if None not in (used, allowed, excess) and excess != max(used - allowed, 0):
            raise ValueError("excess_seconds != max(total_used - allowed, 0)")
        if None not in (excess, self.daily_rate, self.amount_owed):
            expected = excess / 86400 * self.daily_rate
            if abs(self.amount_owed - expected) > MONEY_TOLERANCE:
                raise ValueError(f"amount_owed {self.amount_owed} != {expected:.2f}")
        if self.status == "final" and self.amount_owed is None:
            raise ValueError("final demurrage needs amount_owed")
        return self


class ReviewItem(Schema):
    severity: Literal["blocker", "material", "advisory"]
    code: str
    message: str
    affected_fields: list[str]
    related_source_ids: list[str] = []


class ResolverQuestion(Schema):
    prompt: str
    options: list[str] = Field(min_length=1)


class ResolvedVoyageCore(Schema):
    """Resolver output (one LLM call per voyage). Durations are filled by Python."""

    contract: ContractTerms
    port_visits: list[PortVisit]
    laytime_by_cargo_port: list[CargoPortLaytime]
    questions: list[ResolverQuestion] = []
    notes: list[str] = []


class VoyageAnswer(Schema):
    voyage: VoyageSummary
    emails: list[EmailAssessment]
    contract: ContractTerms
    port_visits: list[PortVisit]
    laytime_by_cargo_port: list[CargoPortLaytime]
    latest_position: LatestPosition | None
    demurrage: DemurrageAnswer
    review_items: list[ReviewItem]
    evidence_index: list[EvidenceRef] = []

    @model_validator(mode="after")
    def _laytime_only_for_cargo_visits(self) -> VoyageAnswer:
        cargo = {v.visit_id for v in self.port_visits if v.laytime_applies}
        if extra := {lt.visit_id for lt in self.laytime_by_cargo_port} - cargo:
            raise ValueError(f"laytime rows for non-cargo visits: {sorted(extra)}")
        ids = [e.source_id for e in self.emails]
        if len(ids) != len(set(ids)):
            raise ValueError("each email must be assessed exactly once")
        return self
