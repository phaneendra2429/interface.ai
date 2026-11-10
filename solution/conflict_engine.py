"""
Dynamic Conflict Detection & Resolution Engine.
Identifies intra-snapshot contradictions and cross-snapshot rate/term shifts
purely from the ingested Snapshot facts and documents.
Adheres strictly to ARCHITECTURE_AND_DESIGN.md v5.0.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional, Any

# Safe centralized contract import guard
if "contract" not in sys.modules:
    harness_dir = next((d / "harness" for d in Path(__file__).resolve().parents if (d / "harness").is_dir()), None)
    if harness_dir is not None and str(harness_dir) not in sys.path:
        sys.path.insert(0, str(harness_dir))

try:
    from contract import Conflict, Source
except ImportError:
    from models import Conflict, Source

try:
    from models import ExtractedFact, TypedValue
    from policy import EligibilityEngine
except ImportError:
    from solution.models import ExtractedFact, TypedValue
    from solution.policy import EligibilityEngine


class ConflictEngine:
    def __init__(self):
        pass

    def build_conflicts(self, ledger: Any) -> list[Conflict]:
        """Detect and consolidate all qualified contradictions across and within ingested snapshots."""
        if not ledger or not getattr(ledger, "snapshots", None) or len(ledger.snapshots) == 0:
            return []

        # Collect all facts across all ingested snapshots
        all_facts: list[ExtractedFact] = []
        for snap_rec in ledger.chronological_snapshots:
            all_facts.extend(snap_rec.facts)

        if not all_facts:
            return []

        # Group facts by qualified comparison signature including product_name:
        groups = defaultdict(list)
        for fact in all_facts:
            ident = fact.identity
            sig = (
                ident.entity,
                ident.product_name,
                ident.attribute,
                ident.account_category,
                ident.transaction_type,
                ident.is_jumbo,
                ident.is_special,
                ident.is_intro,
                ident.term_months,
                ident.reporting_period,
            )
            groups[sig].append(fact)

        raw_conflicts: list[Conflict] = []
        latest_date = ledger.latest_date or "9999-99-99"
        active_snapshot_id = ledger.chronological_snapshots[-1].snapshot_id if ledger.chronological_snapshots else None

        for sig, facts in groups.items():
            entity, product_name, attribute, cat, tx_type, is_jumbo, is_special, is_intro, term, rep_period = sig

            # Partition facts by interval compatibility using exact elementary shared segments for model years
            # so a historical band (e.g. 2023-2026) never transitively merges disjoint current tiers (2021-2023 and 2024-2027).
            labeled_buckets: list[tuple[str, list[ExtractedFact]]] = []

            year_facts = [f for f in facts if f.identity.tier_interval is not None and f.identity.tier_interval.unit == "year"]
            dollar_facts = [f for f in facts if f.identity.tier_interval is not None and f.identity.tier_interval.unit == "$"]
            unbounded_facts = [f for f in facts if f.identity.tier_interval is None]

            if year_facts:
                # Collect all integer model years referenced by year_facts
                candidate_years: set[int] = set()
                for yf in year_facts:
                    ti = yf.identity.tier_interval
                    if ti is None:
                        continue
                    if ti.low is not None and ti.high is not None:
                        for y in range(int(ti.low), int(ti.high) + 1):
                            candidate_years.add(y)
                    elif ti.low is None and ti.high is not None:
                        candidate_years.add(int(ti.high))
                    elif ti.low is not None and ti.high is None:
                        candidate_years.add(int(ti.low))

                sorted_years = sorted(candidate_years)
                # Map each year to the exact list of covering year_facts
                segments: list[tuple[int, int, list[ExtractedFact]]] = []
                for y in sorted_years:
                    cov = [
                        yf for yf in year_facts
                        if yf.identity.tier_interval is not None and yf.identity.tier_interval.contains(float(y))
                    ]
                    if not cov:
                        continue
                    cov_ids = tuple(id(x) for x in cov)
                    if segments and tuple(id(x) for x in segments[-1][2]) == cov_ids and segments[-1][1] == y - 1:
                        segments[-1] = (segments[-1][0], y, cov)
                    else:
                        segments.append((y, y, cov))

                for idx_s, (y_start, y_end, seg_facts) in enumerate(segments):
                    bucket_list = list(seg_facts)
                    # Unbounded calculator/promo facts apply only to the newest model-year segment
                    if unbounded_facts and idx_s == len(segments) - 1:
                        bucket_list.extend(unbounded_facts)
                    seg_label = f"{y_start}" if y_start == y_end else f"{y_start}-{y_end}"
                    labeled_buckets.append((seg_label, bucket_list))
            elif unbounded_facts:
                labeled_buckets.append(("", unbounded_facts))

            if dollar_facts:
                dollar_map: dict[tuple[Optional[float], Optional[float]], list[ExtractedFact]] = defaultdict(list)
                for df in dollar_facts:
                    ti = df.identity.tier_interval
                    if ti is not None:
                        dollar_map[(ti.low, ti.high)].append(df)
                for _, d_grp in dollar_map.items():
                    lbl = d_grp[0].identity.tier_interval.raw_label if d_grp[0].identity.tier_interval else ""
                    labeled_buckets.append((lbl, d_grp))

            for seg_label, bucket in labeled_buckets:
                # Group by TypedValue equality to distinguish 12.50%-19.50% from 12.50%-21.00%
                # and treat 3.5% == 3.50% without float-conversion crashes
                typed_groups: list[tuple[TypedValue, list[ExtractedFact]]] = []

                for f in bucket:
                    if f.withdrawn:
                        continue
                    tv = f.typed_value
                    if tv is None:
                        is_r = "-" in f.value_str or "–" in f.value_str
                        tv = TypedValue(is_range=is_r, low=f.value_num or 0.0, high=None, unit=f.unit, raw_str=f.value_str)

                    matched_group = None
                    for grp_tv, grp_facts in typed_groups:
                        if grp_tv.equals(tv):
                            matched_group = grp_facts
                            break
                    if matched_group is not None:
                        matched_group.append(f)
                    else:
                        typed_groups.append((tv, [f]))

                if len(typed_groups) < 2:
                    continue

                valid_typed_groups = typed_groups

                # Gather representative sources and values
                conflict_sources: list[Source] = []
                best_fact_per_val: list[ExtractedFact] = []
                valid_values: list[str] = []

                for tv, grp_facts in valid_typed_groups:
                    # Prefer active-snapshot facts when choosing representative source for each competing value
                    grp_facts.sort(
                        key=lambda f: (
                            1 if (active_snapshot_id and f.snapshot_id == active_snapshot_id) else 0,
                            EligibilityEngine.rank_key(f),
                        ),
                        reverse=True,
                    )
                    best_f = grp_facts[0]
                    best_fact_per_val.append(best_f)
                    valid_values.append(best_f.value_str)
                    conflict_sources.append(Source(
                        url=best_f.source_url,
                        snapshot_id=best_f.snapshot_id,
                        quote=best_f.raw_quote,
                    ))

                # Determine resolution using unified EligibilityEngine policy on the full bucket
                winning_fact = EligibilityEngine.get_winning_fact(
                    bucket,
                    target_date=latest_date,
                    cutoff_date=latest_date,
                    active_snapshot_id=active_snapshot_id,
                )

                if winning_fact is None:
                    resolution = None
                    reason = "The active snapshot has no eligible, unambiguous winner."
                else:
                    resolution = winning_fact.value_str
                    if winning_fact.authority_score >= 0.9:
                        reason = f"Authoritative primary rate sheet ({winning_fact.source_url}) supersedes secondary or promotional pages."
                    else:
                        reason = f"Resolved to the latest active rate observed on {winning_fact.taken_on}."

                # Construct topic string
                topic_parts = []
                if is_special:
                    topic_parts.append("special")
                if is_jumbo:
                    topic_parts.append("jumbo")
                if term:
                    topic_parts.append(f"{term}-month")
                if tx_type:
                    topic_parts.append(tx_type)
                if product_name:
                    topic_parts.append(product_name.replace("_", " "))
                else:
                    topic_parts.append(entity.replace("_", " "))
                topic_parts.append(attribute.replace("_", " "))

                if seg_label:
                    topic_parts.append(f"({seg_label})")

                topic = " ".join(topic_parts)

                raw_conflicts.append(Conflict(
                    topic=topic,
                    values=valid_values,
                    sources=conflict_sources,
                    resolution=resolution,
                    reason=reason,
                ))

        # Pass 3: Deduplicate conflicts with identical competing source/value sets or (topic, values)
        deduped: list[Conflict] = []
        seen = set()
        seen_source_sets = set()
        for c in raw_conflicts:
            c_key = (c.topic, frozenset(c.values))
            base_topic = c.topic.split(" (")[0]
            src_key = (base_topic, frozenset(c.values), frozenset((s.url, s.snapshot_id, s.quote) for s in c.sources))
            if c_key not in seen and src_key not in seen_source_sets:
                seen.add(c_key)
                seen_source_sets.add(src_key)
                deduped.append(c)

        return deduped
