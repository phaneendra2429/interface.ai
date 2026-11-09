"""
Data models for the Knowledge System.
Adheres strictly to ARCHITECTURE_AND_DESIGN.md v5.0.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Safe centralized contract import guard (handles both in-repo and detached grading environments)
if "contract" not in sys.modules:
    harness_dir = next((d / "harness" for d in Path(__file__).resolve().parents if (d / "harness").is_dir()), None)
    if harness_dir is not None and str(harness_dir) not in sys.path:
        sys.path.insert(0, str(harness_dir))

try:
    from contract import Answer, Conflict, KnowledgeSystem, Page, Snapshot, Source
except ImportError:
    # Minimal fallback stubs if contract is loaded dynamically by the harness
    @dataclass
    class Source:
        url: str
        snapshot_id: Optional[str] = None
        quote: Optional[str] = None

    @dataclass
    class Answer:
        text: str
        sources: list[Source] = field(default_factory=list)
        confident: bool = True
        conflict_note: Optional[str] = None

    @dataclass
    class Conflict:
        topic: str
        values: list[str] = field(default_factory=list)
        sources: list[Source] = field(default_factory=list)
        resolution: Optional[str] = None
        reason: Optional[str] = None

    @dataclass(frozen=True)
    class Page:
        url: str
        title: str
        markdown: str
        fetched_at: str

    @dataclass(frozen=True)
    class Snapshot:
        snapshot_id: str
        taken_on: str
        pages: list[Page]

    class KnowledgeSystem:
        def ingest(self, snapshot: Snapshot) -> None:
            raise NotImplementedError
        def answer(self, question: str, as_of: Optional[str] = None) -> Answer:
            raise NotImplementedError
        def conflicts(self) -> list[Conflict]:
            raise NotImplementedError


@dataclass(frozen=True)
class TypedValue:
    """Represents a numeric scalar or range with unit and precision-preserving equality."""
    is_range: bool
    low: float
    high: Optional[float] = None
    unit: str = "%"              # "%", "$", ""
    raw_str: str = ""

    def equals(self, other: TypedValue) -> bool:
        if self.unit != other.unit or self.is_range != other.is_range:
            return False
        if not self.is_range:
            return abs(self.low - other.low) < 1e-4
        return abs(self.low - other.low) < 1e-4 and abs((self.high or 0.0) - (other.high or 0.0)) < 1e-4


@dataclass(frozen=True)
class IntervalBound:
    low: Optional[float] = None
    high: Optional[float] = None
    low_inclusive: bool = True
    high_inclusive: bool = True
    unit: str = ""              # "$", "year", "month", "%"
    # presentation metadata excluded from equality and hashing
    raw_label: str = field(default="", compare=False, hash=False)

    def contains(self, val: float) -> bool:
        if self.low is not None:
            if self.low_inclusive and val < self.low:
                return False
            if not self.low_inclusive and val <= self.low:
                return False
        if self.high is not None:
            if self.high_inclusive and val > self.high:
                return False
            if not self.high_inclusive and val >= self.high:
                return False
        return True

    def overlaps(self, other: IntervalBound) -> bool:
        """Check if two intervals overlap. Useful for model-year bands (e.g. 2023-26 vs 2024-27)."""
        if self.unit != other.unit:
            return False
        # Calculate intersection
        s_low = float("-inf") if self.low is None else self.low
        s_high = float("inf") if self.high is None else self.high
        o_low = float("-inf") if other.low is None else other.low
        o_high = float("inf") if other.high is None else other.high

        max_low = max(s_low, o_low)
        min_high = min(s_high, o_high)

        if max_low < min_high:
            return True
        elif max_low == min_high:
            # Overlaps only if both bounds at this exact point are inclusive
            low_inc = self.low_inclusive if max_low == s_low else other.low_inclusive
            high_inc = self.high_inclusive if min_high == s_high else other.high_inclusive
            return low_inc and high_inc
        return False


@dataclass(frozen=True)
class FactIdentity:
    entity: str                 # "mortgage", "auto_loan", "certificate", "checking", "savings", "credit_card", "personal_loan", "debit_card", "heloc", "dupaco"
    product_name: str = ""      # "platinum_visa", "rewards_visa", "business_platinum", "business_rewards", "share_savings", "bright_savings", "1st_rate_checking"
    attribute: str = ""         # "apr", "apy", "dividend_rate", "interest_rate", "replacement_fee", "branch_count"
    account_category: str = "personal"  # "personal", "business", "health_savings", "ira"
    is_jumbo: bool = False
    is_special: bool = False
    is_intro: bool = False
    transaction_type: str = ""  # "purchase", "refinance", ""
    term_months: Optional[int] = None
    tier_interval: Optional[IntervalBound] = None
    reporting_period: Optional[str] = None  # e.g., "2023", "2024", "2025", "2026"


@dataclass
class ExtractedFact:
    identity: FactIdentity
    value_str: str              # e.g., "5.39%", "7.13%", "22", "$2.50"
    value_num: Optional[float]  # e.g., 5.39, 7.13, 22.0, 2.50
    unit: str                   # "%", "$", ""
    source_url: str
    snapshot_id: str
    taken_on: str
    authority_score: float
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    footnote_conditions: Optional[str] = None
    footnote_text: Optional[str] = None
    typed_value: Optional[TypedValue] = None
    raw_quote: str = ""         # exact verbatim substring from page.markdown for Source.quote


@dataclass
class DocumentSection:
    url: str
    snapshot_id: str
    heading: str
    raw_markdown: str           # exact verbatim slice from page.markdown
    clean_markdown: str


@dataclass
class PageRecord:
    url: str
    clean_path: str
    title: str
    markdown: str
    fetched_at: str
    snapshot_id: str
    taken_on: str
    authority_score: float
