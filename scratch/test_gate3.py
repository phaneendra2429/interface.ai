import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "harness"))

from contract import Page, Snapshot
from solution.ledger import TemporalLedger
from solution.conflict_engine import ConflictEngine
from scoring import score_conflicts

def test_empty_ledger():
    engine = ConflictEngine()
    empty_ledger = TemporalLedger()
    conflicts = engine.build_conflicts(empty_ledger)
    assert conflicts == [], f"Expected empty list for empty ledger, got {conflicts}"
    print("[PASS] Empty ledger returns []")

def test_selftest_conflicts():
    ledger = TemporalLedger()
    engine = ConflictEngine()

    p1 = Page(url="https://example.com/certificates", title="Certificates", markdown="# Certificates\n\n6-month certificate APY: 2.95%\n\n12-month certificate APY: 3.10%", fetched_at="2026-01-05T00:00:00Z")
    p2 = Page(url="https://example.com/fees", title="Fees", markdown="# Fees\n\nLost debit card replacement fee: $2.50", fetched_at="2026-01-05T00:00:00Z")
    s1 = Snapshot(snapshot_id="01_2026-01-05_baseline", taken_on="2026-01-05", pages=[p1, p2])
    ledger.ingest_snapshot(s1)

    p3 = Page(url="https://example.com/certificates", title="Certificates", markdown="# Certificates\n\n6-month certificate APY: 3.50%\n\n12-month certificate APY: 3.10%", fetched_at="2026-03-01T00:00:00Z")
    p4 = Page(url="https://example.com/promo", title="Promo", markdown="# Promo\n\nSummer special: 6-month certificate APY 2.95% (offer ended 2025-09-30)", fetched_at="2026-03-01T00:00:00Z")
    s2 = Snapshot(snapshot_id="02_2026-03-01_update", taken_on="2026-03-01", pages=[p3, p4])
    ledger.ingest_snapshot(s2)

    conflicts = engine.build_conflicts(ledger)
    print(f"Selftest conflicts found: {len(conflicts)}")
    for c in conflicts:
        print(f"  Topic: {c.topic}, Values: {c.values}, Res: {c.resolution}")

    gold = json.loads((repo_root / "harness" / "selftest" / "conflicts_gold.json").read_text(encoding="utf-8"))
    res = score_conflicts(gold, conflicts)
    print(f"Selftest conflict score: {res}")
    assert res["recall"] == 1.0, f"Expected recall 1.0, got {res['recall']}"
    assert res["resolution_accuracy"] == 1.0, f"Expected resolution accuracy 1.0, got {res['resolution_accuracy']}"
    print("[PASS] Selftest conflict scoring passed")

def test_counterfactual_mutation():
    """Verify that changing a rate dynamically updates the conflict without code changes."""
    ledger = TemporalLedger()
    engine = ConflictEngine()

    p1 = Page(url="https://custom-bank.com/rates", title="Rates", markdown="# Rates\n\n6-month certificate APY: 2.95%", fetched_at="2026-01-05T00:00:00Z")
    s1 = Snapshot(snapshot_id="snap_alpha", taken_on="2026-01-05", pages=[p1])
    ledger.ingest_snapshot(s1)

    # Mutate to 8.25%
    p2 = Page(url="https://custom-bank.com/rates", title="Rates", markdown="# Rates\n\n6-month certificate APY: 8.25%", fetched_at="2026-03-01T00:00:00Z")
    s2 = Snapshot(snapshot_id="snap_beta", taken_on="2026-03-01", pages=[p2])
    ledger.ingest_snapshot(s2)

    conflicts = engine.build_conflicts(ledger)
    assert len(conflicts) >= 1, "Expected at least 1 conflict for mutated rate"
    c = conflicts[0]
    print(f"Mutated conflict: {c.topic}, values={c.values}, resolution={c.resolution}")
    assert "8.25%" in c.values, f"Mutated rate 8.25% must appear in conflict values: {c.values}"
    assert c.resolution == "8.25%", f"Resolution must be 8.25%, got {c.resolution}"
    assert any(s.snapshot_id == "snap_beta" for s in c.sources), "Source must cite mutated snapshot_id"
    assert any("custom-bank.com" in s.url for s in c.sources), "Source must cite mutated URL"
    print("[PASS] Dynamic counterfactual mutation test passed")

if __name__ == "__main__":
    test_empty_ledger()
    test_selftest_conflicts()
    test_counterfactual_mutation()
    print("GATE 3 VERIFIED SUCCESSFULLY!")
