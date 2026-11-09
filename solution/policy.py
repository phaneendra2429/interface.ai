"""
Shared Ingestion, Eligibility, and Decision Policy.
Unified policy shared across answer() and conflicts().
Adheres strictly to ARCHITECTURE_AND_DESIGN.md v5.0.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Optional

# Safe centralized contract import guard
if "contract" not in sys.modules:
    harness_dir = next((d / "harness" for d in Path(__file__).resolve().parents if (d / "harness").is_dir()), None)
    if harness_dir is not None and str(harness_dir) not in sys.path:
        sys.path.insert(0, str(harness_dir))

try:
    from models import ExtractedFact
except ImportError:
    from solution.models import ExtractedFact


class EligibilityEngine:
    """Unified policy shared across answer() and conflicts()."""

    @staticmethod
    def is_eligible(fact: ExtractedFact, target_date: str, cutoff_date: str) -> bool:
        """Evaluate temporal eligibility under 4-clock constraints.
        1. Crawl visibility: must have been observed on or before cutoff_date.
        2. Offer expiry: if explicitly expired before target_date, strictly ineligible as active.
        3. Offer effective date: if effective_date is in the future relative to target_date, ineligible.
        """
        if fact.taken_on > cutoff_date:
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
        When active_snapshot_id is provided, facts present in the active snapshot supersede
        facts from pages removed in earlier snapshots.
        """
        eligible = [f for f in facts if cls.is_eligible(f, target_date, cutoff_date)]
        if not eligible:
            return None
        if active_snapshot_id is not None:
            active_eligible = [f for f in eligible if f.snapshot_id == active_snapshot_id]
            if active_eligible:
                eligible = active_eligible
        val_counts = Counter((f.authority_score, f.value_str) for f in eligible)
        eligible.sort(
            key=lambda f: (cls.rank_key(f), val_counts[(f.authority_score, f.value_str)]),
            reverse=True,
        )
        return eligible[0]
