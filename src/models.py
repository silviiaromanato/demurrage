"""Pydantic schemas for every pipeline stage.

Each model validates its own internal consistency on construction and on load
from JSON. Checks that need data from another stage live in `validate.py`.

The resolver models double as the LLM's JSON schema, so field descriptions
follow DOMAIN.md. Fields Python fills after the resolver are typed `Computed`
and left out of that schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from pydantic.json_schema import SkipJsonSchema

Relevance = Literal["relevant", "not_relevant", "uncertain"]
AnswerStatus = Literal["final", "provisional", "incomplete"]
ContractStatus = Literal["resolved", "missing", "conflicted", "provisional"]
PortVisitStatus = Literal["final", "provisional", "missing", "conflicted"]
StoppageCause = Literal[
    "vessel", "terminal", "berth_waiting", "shifting", "weather", "other"
]
Seconds = int | None
Computed = Annotated[Seconds, SkipJsonSchema(), Field(ge=0)]
ComputedText = Annotated[str | None, SkipJsonSchema()]
ComputedFraction = Annotated[float | None, SkipJsonSchema(), Field(ge=0, le=1)]
ComputedHours = Annotated[float | None, SkipJsonSchema(), Field(gt=0)]

TopicFlag = Literal["yes", "no", "uncertain"]

TOPICS = ("contract", "schedule", "laytime", "position", "other")
VESSEL_UNCERTAIN = "uncertain"
NOT_OUR_VESSEL = "not_our_vessel"
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


# --- Stage 2: classify (LLM, three calls per email) ---


class VoyageCheck(Schema):
    """Call 1: is this email about a vessel voyage at all?"""

    choice: Literal["yes", "no", "uncertain"]
    confidence: float | None = None
    probabilities: dict[str, float] = {}

    @classmethod
    def from_jev(cls, answer: dict[str, Any]) -> VoyageCheck:
        return cls(
            choice=answer["choice"],
            confidence=answer.get("confidence"),
            probabilities=answer.get("probabilities") or {},
        )


class VesselCheck(Schema):
    """Call 2: which fleet vessel (`unknown` if not stated)."""

    choice: str
    confidence: float | None = None
    probabilities: dict[str, float] = {}

    @classmethod
    def from_jev(cls, answer: dict[str, Any]) -> VesselCheck:
        return cls(
            choice=answer["choice"],
            confidence=answer.get("confidence"),
            probabilities=answer.get("probabilities") or {},
        )


class TopicCheck(Schema):
    """Call 3: which topics the email discusses."""

    contract: TopicFlag
    schedule: TopicFlag
    laytime: TopicFlag
    position: TopicFlag
    other: TopicFlag
    confidence: dict[str, float] = {}


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


class EvidenceRef(Schema):
    """The model cites line numbers; Python copies those lines into `quote`."""

    source_id: str = Field(description="source_id of the cited email.")
    first_line: int = Field(ge=1, description="First cited line number of the body.")
    last_line: int = Field(
        ge=1, description="Last cited line number (same as first_line for one line)."
    )
    quote: ComputedText = None


class EmailExtraction(Schema):
    model_config = ConfigDict(extra="ignore")

    source_id: str
    filename: str
    content_hash: str
    message_id: str | None
    subject: str | None
    sent_at: str | None
    vessel_name: str | None = None
    voyage_about: Literal["yes", "no", "uncertain"] = "uncertain"
    topics: dict[str, TopicFlag] = Field(
        default_factory=lambda: dict.fromkeys(TOPICS, "uncertain")
    )
    warnings: list[str] = []
    extraction_status: Literal["pending", "complete"] = "pending"
    review: EmailReview | None = None
    answered_reviews: list[EmailReview] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def affects_schedule(self) -> bool:
        return self.topics.get("schedule", "no") != "no"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def affects_laytime(self) -> bool:
        return self.topics.get("laytime", "no") != "no"

    @model_validator(mode="after")
    def _topics_are_complete(self) -> EmailExtraction:
        if set(self.topics) != set(TOPICS):
            raise ValueError(f"topics must have exactly the keys {TOPICS}")
        return self


# --- Stage 3: resolve (LLM, one call per voyage) and the public answer ---


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
    relevance: Relevance
    affects_schedule: bool
    affects_laytime: bool
    topics: dict[str, TopicFlag]
    reason: str


class WrittenTime(Schema):
    """A time exactly as an email states it. Python adds the UTC offset."""

    value: str = Field(
        description=(
            "Date and clock time as written, formatted YYYY-MM-DDTHH:MM with "
            "no offset. Never convert time zones or add or subtract hours."
        )
    )
    basis: Literal["local", "utc"] = Field(
        description=(
            "utc for a master's report labelled UTC/GMT/Z; local for a SOF "
            "or agent's notice (port local time). Copy the label; do not convert."
        )
    )


class DeductionRule(Schema):
    kind: str = Field(
        description=(
            "What the rule covers, e.g. shifting from anchorage to berth, "
            "vessel breakdown, terminal delay."
        )
    )
    fraction_deducted: float = Field(
        ge=0,
        le=1,
        description=(
            "Share of the stoppage duration to deduct from laytime: 1.0 if the "
            "period does not count at all, 0.5 for 'counts as half laytime', "
            "0.0 if the period counts fully as laytime."
        ),
    )
    evidence: EvidenceRef = Field(description="The contract lines for this rule.")


class Amendment(Schema):
    term: str = Field(description="Contract term changed by the addendum.")
    old_value: str | None = Field(
        description="Previous value from the recap, if stated."
    )
    new_value: str = Field(description="Value after the addendum.")
    evidence: EvidenceRef = Field(description="The addendum lines for this change.")


class ContractTerms(Schema):
    status: Annotated[ContractStatus, SkipJsonSchema()] = "missing"
    laytime_allowance: float | None = Field(
        default=None,
        gt=0,
        description="Total laytime allowance, the number as written in the recap.",
    )
    laytime_allowance_unit: Literal["hours", "days"] | None = Field(
        default=None,
        description="Unit of laytime_allowance as written. Do not convert.",
    )
    laytime_allowance_hours: ComputedHours = None
    reversible: bool | None = Field(
        default=None,
        description=(
            "True if used laytime at all load and discharge ports is summed "
            "against one shared allowance."
        ),
    )
    turn_time_after_nor_hours: float | None = Field(
        default=None,
        ge=0,
        description="Hours added after NOR tendered before laytime starts.",
    )
    laytime_end_event: str | None = Field(
        default=None,
        description=(
            "Event that ends laytime at a port, usually hoses disconnected "
            "after cargo operations; quote recap wording."
        ),
    )
    demurrage_rate_per_day: float | None = Field(
        default=None,
        gt=0,
        description="Demurrage rate per day in contract currency.",
    )
    currency: str | None = Field(
        default=None, description="Currency code for demurrage (e.g. USD)."
    )
    pro_rata: bool | None = Field(
        default=None,
        description="True if partial days are charged proportionally (PDPR).",
    )
    deduction_rules: list[DeductionRule] = Field(
        default=[], description="Laytime deduction rules from recap and addenda."
    )
    exceptions_continue_on_demurrage: bool | None = Field(
        default=None,
        description=(
            "Whether deductions still apply after laytime allowance is "
            "exhausted; null if recap is silent."
        ),
    )
    amendments: list[Amendment] = Field(
        default=[], description="Each addendum replaces only the term it amends."
    )
    evidence: list[EvidenceRef] = Field(
        default=[], description="Quotes supporting each material contract field."
    )

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

    visit_id: str = Field(description="visit_id from the supplied itinerary.")
    port: str
    function: str
    arrival_written: WrittenTime | None = Field(
        default=None,
        description=(
            "Actual EOSP (end of sea passage). Not anchored, NOR or all-fast. "
            "Not an ETA."
        ),
    )
    departure_written: WrittenTime | None = Field(
        default=None, description="Actual sailed / COSP time. Not an ETD."
    )
    arrival: ComputedText = None
    departure: ComputedText = None
    laytime_applies: bool = False
    status: PortVisitStatus = Field(
        default="missing",
        description=(
            "final from final SOF or master actual report; provisional from "
            "interim SOF or agent notice; missing if only estimates exist."
        ),
    )
    evidence: list[EvidenceRef] = Field(
        default=[], description="Quotes for arrival and departure times."
    )


class Stoppage(Schema):
    description: str = Field(description="What happened during the stoppage.")
    cause: StoppageCause = Field(description="Primary cause category.")
    start_written: WrittenTime | None = Field(
        default=None, description="Stoppage start."
    )
    end_written: WrittenTime | None = Field(default=None, description="Stoppage end.")
    start: ComputedText = None
    end: ComputedText = None
    deduction_rule: str | None = Field(
        default=None,
        description=(
            "kind of the supplied deduction rule that applies; null if none "
            "applies (the stoppage then counts in full)."
        ),
    )
    fraction_deducted: ComputedFraction = None
    evidence: list[EvidenceRef] = Field(
        default=[], description="SOF or notice quotes for the stoppage."
    )

    @model_validator(mode="after")
    def _ends_after_start(self) -> Stoppage:
        if self.start and self.end:
            if datetime.fromisoformat(self.end) < datetime.fromisoformat(self.start):
                raise ValueError(f"stoppage ends before it starts: {self.description}")
        return self


class CargoPortLaytime(Schema):
    visit_id: str = Field(description="Cargo port visit_id (load or discharge).")
    port: str
    nor_tendered_written: WrittenTime | None = Field(
        default=None, description="NOR tendered time."
    )
    final_hoses_disconnected_written: WrittenTime | None = Field(
        default=None,
        description="Hoses disconnected after cargo operations; ends laytime.",
    )
    nor_tendered: ComputedText = None
    laytime_start: ComputedText = None
    final_hoses_disconnected: ComputedText = None
    gross_duration_seconds: Computed = None
    stoppages: list[Stoppage] = []
    deducted_duration_seconds: Computed = None
    used_duration_seconds: Computed = None
    calculation_notes: list[str] = Field(
        default=[],
        description="How events were chosen; conflicts or interim sources.",
    )
    status: PortVisitStatus = Field(
        default="missing",
        description="final only when final SOF reports hoses disconnected.",
    )
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
    reported_at_written: WrittenTime | None = Field(
        default=None,
        description=(
            "When the vessel was observed at this position: the time in the "
            "position report, not the email Date header or sent_at."
        )
    )
    reported_at: ComputedText = None
    description: str | None = Field(
        description="Where the vessel was, in the email's own words."
    )
    evidence: list[EvidenceRef] = Field(
        default=[],
        description="Cited lines of the position report with the latest reported_at.",
    )


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


class ContractDraft(Schema):
    """Contract terms from the voyage emails classified as contract."""

    contract: ContractTerms
    questions: list[ResolverQuestion] = Field(
        default=[], description="Questions for a human when contract sources disagree."
    )
    notes: list[str] = []


class LaytimeDraft(Schema):
    """Port calls and cargo laytime from the voyage's non-contract emails."""

    port_visits: list[PortVisit] = Field(
        description="One row per itinerary visit, in itinerary order."
    )
    laytime_by_cargo_port: list[CargoPortLaytime] = Field(
        description=(
            "One row per load or discharge visit. Stoppages name the supplied "
            "deduction rule that applies."
        )
    )
    latest_position: LatestPosition | None = Field(
        default=None,
        description=(
            "The position report with the latest reported_at. Email sent time "
            "is not used. Null if no position report exists."
        ),
    )
    questions: list[ResolverQuestion] = Field(
        default=[], description="Questions for a human when port sources disagree."
    )
    notes: list[str] = []


class ResolvedVoyageCore(Schema):
    """Assembled from the contract call and the laytime call. Durations stay null."""

    contract: ContractTerms
    port_visits: list[PortVisit] = Field(
        description="One row per itinerary visit, in itinerary order."
    )
    laytime_by_cargo_port: list[CargoPortLaytime] = Field(
        description="One row per load or discharge visit."
    )
    latest_position: LatestPosition | None = None
    questions: list[ResolverQuestion] = Field(
        default=[], description="Questions for a human when sources disagree."
    )
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

    @model_validator(mode="after")
    def _laytime_only_for_cargo_visits(self) -> VoyageAnswer:
        cargo = {v.visit_id for v in self.port_visits if v.laytime_applies}
        if extra := {lt.visit_id for lt in self.laytime_by_cargo_port} - cargo:
            raise ValueError(f"laytime rows for non-cargo visits: {sorted(extra)}")
        ids = [e.source_id for e in self.emails]
        if len(ids) != len(set(ids)):
            raise ValueError("each email must be assessed exactly once")
        return self
