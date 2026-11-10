"""
Main submission entrypoint for the Hackathon Knowledge System.
Adheres strictly to harness/contract.py and ARCHITECTURE_AND_DESIGN.md v5.0.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Safe centralized contract import guard
if "contract" not in sys.modules:
    harness_dir = next((d / "harness" for d in Path(__file__).resolve().parents if (d / "harness").is_dir()), None)
    if harness_dir is not None and str(harness_dir) not in sys.path:
        sys.path.insert(0, str(harness_dir))

try:
    from contract import Answer, Conflict, KnowledgeSystem, Snapshot
except ImportError:
    from models import Answer, Conflict, KnowledgeSystem, Snapshot

try:
    from conflict_engine import ConflictEngine
    from ledger import TemporalLedger
    from resolver import AnswerResolver
except ImportError:
    from solution.conflict_engine import ConflictEngine
    from solution.ledger import TemporalLedger
    from solution.resolver import AnswerResolver


class DupacoKnowledgeSystem:
    def __init__(self):
        self.ledger = TemporalLedger()
        self.resolver = AnswerResolver(self.ledger)
        self.conflict_engine = ConflictEngine()

    def ingest(self, snapshot: Snapshot) -> None:
        """Called once per snapshot, oldest first. Must finish before next call."""
        self.ledger.ingest_snapshot(snapshot)

    def answer(self, question: str, as_of: Optional[str] = None) -> Answer:
        """Answer a member's question.
        as_of=None  -> answer from the LATEST snapshot.
        as_of=date  -> answer as the website stood on that date.
        """
        return self.resolver.resolve(question, as_of=as_of)

    def conflicts(self) -> list[Conflict]:
        """List site-wide contradictions found across or within snapshots."""
        return self.conflict_engine.build_conflicts(self.ledger)


def build() -> KnowledgeSystem:
    return DupacoKnowledgeSystem()
