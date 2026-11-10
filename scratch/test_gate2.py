import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "harness"))

from contract import Page, Snapshot
from solution.ledger import TemporalLedger

def test_selftest_ingestion():
    ledger = TemporalLedger()
    
    # Simulate selftest snapshot 01
    p1 = Page(url="https://example.com/certificates", title="Certificates", markdown="# Certificates\n\n6-month certificate APY: 2.95%\n\n12-month certificate APY: 3.10%", fetched_at="2026-01-05T00:00:00Z")
    p2 = Page(url="https://example.com/fees", title="Fees", markdown="# Fees\n\nLost debit card replacement fee: $2.50", fetched_at="2026-01-05T00:00:00Z")
    s1 = Snapshot(snapshot_id="01_2026-01-05_baseline", taken_on="2026-01-05", pages=[p1, p2])
    ledger.ingest_snapshot(s1)

    rec1 = ledger.snapshots["01_2026-01-05_baseline"]
    assert len(rec1.facts) >= 3, f"Expected at least 3 facts, got {len(rec1.facts)}"
    
    # Check 6m cert
    cert_6m = [f for f in rec1.facts if f.identity.entity == "certificate" and f.identity.term_months == 6]
    assert cert_6m, "6m cert fact missing"
    assert cert_6m[0].value_str == "2.95%", f"Expected 2.95%, got {cert_6m[0].value_str}"
    assert cert_6m[0].raw_quote in p1.markdown, "Raw quote must be substring of markdown"

    # Check fee
    fee_fact = [f for f in rec1.facts if f.identity.entity == "debit_card"]
    assert fee_fact, "Debit card fee missing"
    assert fee_fact[0].value_str == "$2.50", f"Expected $2.50, got {fee_fact[0].value_str}"
    assert fee_fact[0].raw_quote in p2.markdown

    # Simulate selftest snapshot 02
    p3 = Page(url="https://example.com/certificates", title="Certificates", markdown="# Certificates\n\n6-month certificate APY: 3.50%\n\n12-month certificate APY: 3.10%", fetched_at="2026-03-01T00:00:00Z")
    p4 = Page(url="https://example.com/promo", title="Promo", markdown="# Promo\n\nSummer special: 6-month certificate APY 2.95% (offer ended 2025-09-30)", fetched_at="2026-03-01T00:00:00Z")
    s2 = Snapshot(snapshot_id="02_2026-03-01_update", taken_on="2026-03-01", pages=[p3, p4])
    ledger.ingest_snapshot(s2)

    rec2 = ledger.snapshots["02_2026-03-01_update"]
    promo_fact = [f for f in rec2.facts if f.identity.is_special]
    assert promo_fact, "Promo special fact missing"
    assert promo_fact[0].expiry_date == "2025-09-30", f"Expected expiry 2025-09-30, got {promo_fact[0].expiry_date}"

    print("[PASS] Selftest ingestion test passed")

def test_dupaco_snapshot_05():
    ledger = TemporalLedger()
    
    snap05_dir = repo_root / "snapshots" / "05_2026-09-28_latest" / "pages"
    if not snap05_dir.exists():
        print("[SKIP] Snapshot 05 directory not found")
        return

    pages = []
    key_files = [
        "personal-loans-auto-motorcycle-loans-1c94b6.md",
        "personal-savings-account-7ef72e.md",
        "rates-59a9e6.md",
        "checking-offer-lp-a3545a.md",
        "homebuying-guidance-ba0df5.md",
        "personal-loans-home-loans-a5183f.md",
    ]
    for kf in key_files:
        fpath = snap05_dir / kf
        if fpath.exists():
            pages.append(Page(url=f"https://www.dupaco.com/{kf}", title=kf, markdown=fpath.read_text(encoding="utf-8"), fetched_at="2026-09-28T00:00:00Z"))

    s5 = Snapshot(snapshot_id="05_2026-09-28_latest", taken_on="2026-09-28", pages=pages)
    ledger.ingest_snapshot(s5)

    rec5 = ledger.snapshots["05_2026-09-28_latest"]
    print(f"Total extracted facts from key pages: {len(rec5.facts)}")
    assert len(rec5.facts) > 50, f"Expected > 50 facts, got {len(rec5.facts)}"

    # Check 15-year mortgage
    mort_15 = [f for f in rec5.facts if f.identity.entity == "mortgage" and f.identity.term_months == 180 and f.identity.attribute == "apr"]
    assert mort_15, "15-year mortgage APR missing"
    apr_values = [f.value_str for f in mort_15]
    print(f"15-year mortgage APRs found: {apr_values}")
    assert "6.88%" in apr_values, f"Expected 6.88% APR, got {apr_values}"

    # Check 30-year homebuying-guidance stale table
    stale_guidance = [f for f in rec5.facts if "homebuying-guidance" in f.source_url and f.identity.term_months == 360 and f.identity.attribute == "apr"]
    assert stale_guidance, "Stale guidance mortgage facts missing"
    guidance_aprs = [f.value_str for f in stale_guidance]
    print(f"Guidance 30-year APRs: {guidance_aprs}")
    assert "6.037%" in guidance_aprs, f"Expected 6.037% APR, got {guidance_aprs}"

    # Verify quote provenance: every single fact quote MUST be in its raw page markdown
    pages_by_url = {p.url: p.markdown for p in pages}
    for fact in rec5.facts:
        raw_md = pages_by_url[fact.source_url]
        assert fact.raw_quote in raw_md, f"Quote '{fact.raw_quote}' not found in raw markdown of {fact.source_url}"

    print("[PASS] Dupaco Snapshot 05 key pages extraction & provenance passed")

if __name__ == "__main__":
    test_selftest_ingestion()
    test_dupaco_snapshot_05()
    print("GATE 2 VERIFIED SUCCESSFULLY!")
