"""LLM-facing drafts for the voyage resolver (strict JSON schema output).

Merged onto `itinerary_skeleton` and public models in `resolver.py`. Field
descriptions follow DOMAIN.md so the JSON schema teaches the model the domain.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from demurrage.models import EvidenceRef, ResolverQuestion, Schema

ContractStatus = Literal["resolved", "missing", "conflicted", "provisional"]
VisitStatus = Literal["final", "provisional", "missing", "conflicted"]
StoppageCause = Literal[
    "vessel",
    "terminal",
    "berth_waiting",
    "shifting",
    "weather",
    "other",
]


class DeductionRuleDraft(Schema):
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
    quote: str = Field(
        min_length=1,
        description="Verbatim contract wording for this rule.",
    )


class AmendmentDraft(Schema):
    term: str = Field(description="Contract term changed by the addendum.")
    old_value: str | None = Field(
        description="Previous value from the recap, if stated."
    )
    new_value: str = Field(description="Value after the addendum.")
    quote: str = Field(
        min_length=1,
        description="Verbatim quote from the addendum email.",
    )


class EvidenceQuote(Schema):
    """Citation the resolver returns; offsets are filled later in Python."""

    source_id: str = Field(
        description="source_id of the email block (header line after ---).",
    )
    quote: str = Field(
        min_length=1,
        description="Verbatim substring from that email body.",
    )

    def to_evidence_ref(self) -> EvidenceRef:
        return EvidenceRef(source_id=self.source_id, quote=self.quote)


class ContractDraft(Schema):
    status: ContractStatus = Field(
        description=(
            "resolved only when laytime allowance, turn time after NOR, "
            "demurrage rate and currency are all present with evidence; "
            "otherwise missing, conflicted or provisional."
        )
    )
    laytime_allowance_hours: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Total laytime allowance for the voyage in hours. Convert days "
            "to hours if the recap gives days."
        ),
    )
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
        default=None,
        description="Currency code for demurrage (e.g. USD).",
    )
    pro_rata: bool | None = Field(
        default=None,
        description="True if partial days are charged proportionally (PDPR).",
    )
    deduction_rules: list[DeductionRuleDraft] = Field(
        default_factory=list,
        description="Laytime deduction rules from recap and addenda.",
    )
    exceptions_continue_on_demurrage: bool | None = Field(
        default=None,
        description=(
            "Whether deductions still apply after laytime allowance is "
            "exhausted; null if recap is silent."
        ),
    )
    amendments: list[AmendmentDraft] = Field(
        default_factory=list,
        description="Each addendum replaces only the term it amends.",
    )
    evidence: list[EvidenceQuote] = Field(
        default_factory=list,
        description="Quotes supporting each material contract field.",
    )
    questions: list[ResolverQuestion] = Field(
        default_factory=list,
        description="Human questions when the recap is unclear.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Resolver notes (not shown to charterer).",
    )


class PortVisitDraft(Schema):
    visit_id: str = Field(
        description="Must match a visit_id from the supplied itinerary."
    )
    arrival: str | None = Field(
        default=None,
        description=(
            "Actual EOSP (end of sea passage) in ISO 8601 with offset. "
            "Not anchored, NOR or all-fast. Not an ETA."
        ),
    )
    departure: str | None = Field(
        default=None,
        description=("Actual sailed / COSP time in ISO 8601 with offset. Not an ETD."),
    )
    status: VisitStatus = Field(
        description=(
            "final from final SOF or master actual report; provisional from "
            "interim SOF or agent notice; missing if only estimates exist."
        )
    )
    evidence: list[EvidenceQuote] = Field(
        default_factory=list,
        description="Quotes for arrival and departure times.",
    )


class PortVisitsDraft(Schema):
    visits: list[PortVisitDraft] = Field(
        default_factory=list,
        description="One row per itinerary visit that has schedule evidence.",
    )
    questions: list[ResolverQuestion] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list,
        description="e.g. ETA/ETC forecasts that must not fill arrival/departure.",
    )


class StoppageDraft(Schema):
    description: str = Field(description="What happened during the stoppage.")
    cause: StoppageCause = Field(description="Primary cause category.")
    start: str | None = Field(
        default=None,
        description="Stoppage start, ISO 8601 with offset.",
    )
    end: str | None = Field(
        default=None,
        description="Stoppage end, ISO 8601 with offset.",
    )
    fraction_deducted: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "From contract deduction_rules; 0 if terminal/berth waiting "
            "counts as laytime unless contract says otherwise."
        ),
    )
    contract_rule_quote: str | None = Field(
        default=None,
        description="Quote of the deduction rule applied, if any.",
    )
    evidence: list[EvidenceQuote] = Field(
        default_factory=list,
        description="SOF or notice quotes for the stoppage.",
    )


class LaytimePortDraft(Schema):
    visit_id: str = Field(description="Cargo port visit_id (load or discharge).")
    nor_tendered: str | None = Field(
        default=None,
        description="NOR tendered time, ISO 8601 with offset.",
    )
    final_hoses_disconnected: str | None = Field(
        default=None,
        description=(
            "Hoses disconnected after cargo operations complete; ends laytime. "
            "ISO 8601 with offset."
        ),
    )
    stoppages: list[StoppageDraft] = Field(default_factory=list)
    status: VisitStatus = Field(
        description="final only when final SOF reports hoses disconnected."
    )
    calculation_notes: list[str] = Field(
        default_factory=list,
        description="How events were chosen; conflicts or interim sources.",
    )
    evidence: list[EvidenceQuote] = Field(default_factory=list)


class LaytimeDraftSet(Schema):
    ports: list[LaytimePortDraft] = Field(
        default_factory=list,
        description="One row per load/discharge visit with laytime evidence.",
    )
    questions: list[ResolverQuestion] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
