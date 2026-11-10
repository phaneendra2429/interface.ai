"""
Shared Ingestion, Eligibility, and Decision Policy.
Unified policy shared across answer() and conflicts().
Adheres strictly to ARCHITECTURE_AND_DESIGN.md v5.0.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

# Safe centralized contract import guard
if "contract" not in sys.modules:
    harness_dir = next((d / "harness" for d in Path(__file__).resolve().parents if (d / "harness").is_dir()), None)
    if harness_dir is not None and str(harness_dir) not in sys.path:
        sys.path.insert(0, str(harness_dir))

try:
    from models import ExtractedFact
    from sanitizer import clean_url_path
except ImportError:
    from solution.models import ExtractedFact
    from solution.sanitizer import clean_url_path


class EligibilityEngine:
    """Unified policy shared across answer() and conflicts()."""

    @staticmethod
    def is_eligible(fact: ExtractedFact, target_date: str, cutoff_date: str) -> bool:
        """Evaluate temporal eligibility under 4-clock constraints.
        1. Crawl visibility: must have been observed on or before cutoff_date.
        2. Offer expiry: if explicitly expired before target_date, strictly ineligible as active.
        3. Offer effective date: if effective_date is in the future relative to target_date, ineligible.
        """
        if fact.taken_on > min(target_date, cutoff_date):
            return False
        if fact.expiry_date and fact.expiry_date < target_date:
            return False
        if fact.effective_date and fact.effective_date > target_date:
            return False
        return True

    @staticmethod
    def rank_key(fact: ExtractedFact):
        """Rank eligible facts by authority hierarchy, then in-text effective date, then crawl recency."""
        return (
            fact.authority_score,
            fact.effective_date or "0000-00-00",
            fact.taken_on,
        )

    @classmethod
    def get_winning_fact(
        cls,
        facts: list[ExtractedFact],
        target_date: str,
        cutoff_date: str,
        active_snapshot_id: Optional[str] = None,
    ) -> Optional[ExtractedFact]:
        """Select the authoritative winning fact according to unified eligibility and ranking rules.
        For current values, the active snapshot is the only eligible crawl.
        """
        eligible = [f for f in facts if cls.is_eligible(f, target_date, cutoff_date)]
        if not eligible:
            return None
        if active_snapshot_id is not None:
            eligible = [f for f in eligible if f.snapshot_id == active_snapshot_id]
        return cls.choose(eligible)

    @classmethod
    def choose(cls, facts: list[ExtractedFact]) -> Optional[ExtractedFact]:
        """Choose from a qualified, eligible set. A genuine tie has no winner."""
        if not facts:
            return None
        superseding = [f for f in facts if f.supersedes]
        if superseding:
            facts = superseding
        strongest_rank = max(map(cls.rank_key, facts))
        by_value = defaultdict(list)
        for fact in facts:
            if cls.rank_key(fact) != strongest_rank:
                continue
            value = fact.typed_value
            key = (
                value.unit, value.is_range, round(value.low, 8),
                None if value.high is None else round(value.high, 8),
            ) if value is not None else (fact.unit, fact.value_str)
            by_value[key].append(fact)
        by_support = defaultdict(list)
        for group in by_value.values():
            support = len({clean_url_path(f.source_url) for f in group})
            by_support[support].append(group)
        strongest = by_support[max(by_support)]
        if len(strongest) != 1:
            return None
        winner = min(strongest[0], key=lambda f: (f.source_url, f.raw_quote, f.value_str))
        return None if winner.withdrawn else winner
