"""
Temporal Multi-Snapshot Ledger.
Maintains versioned documents, sections, and extracted facts across all crawled snapshots.
Adheres strictly to ARCHITECTURE_AND_DESIGN.md v5.0.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

# Safe centralized contract import guard
if "contract" not in sys.modules:
    harness_dir = next((d / "harness" for d in Path(__file__).resolve().parents if (d / "harness").is_dir()), None)
    if harness_dir is not None and str(harness_dir) not in sys.path:
        sys.path.insert(0, str(harness_dir))

try:
    from contract import Page, Snapshot
except ImportError:
    pass

try:
    from models import ExtractedFact, PageRecord, DocumentSection
    from parser import parse_page
except ImportError:
    from solution.models import ExtractedFact, PageRecord, DocumentSection
    from solution.parser import parse_page


@dataclass
class SnapshotRecord:
    snapshot_id: str
    taken_on: str
    pages: dict[str, PageRecord] = field(default_factory=dict)  # url -> PageRecord
    facts: list[ExtractedFact] = field(default_factory=list)
    sections: list[DocumentSection] = field(default_factory=list)


class TemporalLedger:
    def __init__(self):
        self.snapshots: dict[str, SnapshotRecord] = {}  # snapshot_id -> SnapshotRecord
        self.chronological_snapshots: list[SnapshotRecord] = []
        self.earliest_date: Optional[str] = None
        self.latest_date: Optional[str] = None
        self.latest_snapshot_id: Optional[str] = None

    def ingest_snapshot(self, snapshot: Any) -> None:
        """Ingest one snapshot crawl in chronological sequence."""
        taken_on = snapshot.taken_on
        sid = snapshot.snapshot_id

        if self.earliest_date is None or taken_on < self.earliest_date:
            self.earliest_date = taken_on
        if self.latest_date is None or taken_on > self.latest_date:
            self.latest_date = taken_on
            self.latest_snapshot_id = sid

        snap_rec = SnapshotRecord(snapshot_id=sid, taken_on=taken_on)

        for p in snapshot.pages:
            page_rec, facts, sections = parse_page(p, snapshot_id=sid, taken_on=taken_on)
            snap_rec.pages[p.url] = page_rec
            snap_rec.facts.extend(facts)
            snap_rec.sections.extend(sections)

        self.snapshots[sid] = snap_rec
        self.chronological_snapshots.append(snap_rec)
        self.chronological_snapshots.sort(key=lambda s: s.taken_on)

    def get_target_snapshot(self, as_of: Optional[str] = None) -> Optional[SnapshotRecord]:
        """Resolve the active snapshot for a query.
        Returns None if as_of precedes the earliest crawled snapshot (pre-baseline)."""
        if as_of is None:
            if self.latest_snapshot_id:
                return self.snapshots[self.latest_snapshot_id]
            return None

        # Pre-baseline check: if as_of is before our very first crawl, we have zero website evidence
        if self.earliest_date and as_of < self.earliest_date:
            return None

        # Find the latest snapshot taken on or before as_of
        eligible = [s for s in self.chronological_snapshots if s.taken_on <= as_of]
        if not eligible:
            return None
        return eligible[-1]

    def get_eligible_snapshots(self, cutoff_date: Optional[str] = None) -> list[SnapshotRecord]:
        """Return all snapshots with taken_on <= cutoff_date."""
        if cutoff_date is None:
            return list(self.chronological_snapshots)
        return [s for s in self.chronological_snapshots if s.taken_on <= cutoff_date]
