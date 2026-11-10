import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution.models import IntervalBound, FactIdentity, ExtractedFact, DocumentSection
from solution.sanitizer import (
    clean_markdown_text,
    extract_effective_date,
    extract_expiry_date,
    extract_footnotes,
    clean_url_path,
)

def test_interval_overlaps():
    # 2023-2026 vs 2024-2027
    i1 = IntervalBound(low=2023, high=2026, unit="year", raw_label="2023-2026")
    i2 = IntervalBound(low=2024, high=2027, unit="year", raw_label="2024-2027")
    assert i1.overlaps(i2), "2023-26 should overlap 2024-27"
    assert i2.overlaps(i1), "Symmetric overlap failed"

    # Non-overlapping
    i3 = IntervalBound(low=2021, high=2023, unit="year")
    i4 = IntervalBound(low=2024, high=2027, unit="year")
    assert not i3.overlaps(i4), "2021-23 should not overlap 2024-27"

    # Different units
    i5 = IntervalBound(low=2023, high=2026, unit="$")
    assert not i1.overlaps(i5), "Different units must not overlap"

    # Raw label excluded from equality and hashing
    i_a = IntervalBound(low=1000, high=5000, unit="$", raw_label="$1,000 - $5,000")
    i_b = IntervalBound(low=1000, high=5000, unit="$", raw_label="$1,000 – $5,000")
    assert i_a == i_b, "Raw label must not affect equality"
    assert hash(i_a) == hash(i_b), "Raw label must not affect hash"
    print("[PASS] IntervalBound test passed")

def test_sanitizer_preserves_numbers():
    # Test cases that previously broke with flawed regex
    text1 = "| $24,999.99 | 2.50% APY |"
    cleaned1 = clean_markdown_text(text1)
    assert "$24,999.99" in cleaned1, f"Expected $24,999.99 to be preserved, got {cleaned1}"

    text2 = "Models from 2027 are eligible."
    cleaned2 = clean_markdown_text(text2)
    assert "2027" in cleaned2, f"Expected 2027 to be preserved, got {cleaned2}"

    text3 = "Monthly fee is $5.99 |"
    cleaned3 = clean_markdown_text(text3)
    assert "$5.99" in cleaned3, f"Expected $5.99 to be preserved, got {cleaned3}"

    # Footnote stripping on rates
    text4 = "| 5.39% APR1 | 5.24%1 |"
    cleaned4 = clean_markdown_text(text4)
    assert "5.39% APR" in cleaned4, f"Expected 5.39% APR, got {cleaned4}"
    assert "5.24%" in cleaned4, f"Expected 5.24%, got {cleaned4}"
    assert "APR1" not in cleaned4, "Footnote digit 1 should be stripped"
    assert "%1" not in cleaned4, "Footnote digit 1 should be stripped"
    print("[PASS] Sanitizer numeric preservation test passed")

def test_date_and_footnote_extraction():
    sample = """
    Rates are effective as of 09/25/2026.
    Special promotion offer ended 2025-09-30.
    
    * 1 Rates are subject to change without notice.
    * 2 APY is accurate as of the effective date.
    """
    eff = extract_effective_date(sample)
    exp = extract_expiry_date(sample)
    fn = extract_footnotes(sample)

    assert eff == "2026-09-25", f"Expected 2026-09-25, got {eff}"
    assert exp == "2025-09-30", f"Expected 2025-09-30, got {exp}"
    assert "1" in fn and "Rates are subject to change" in fn["1"]
    assert "2" in fn and "accurate as of" in fn["2"]
    print("[PASS] Date and footnote extraction test passed")

if __name__ == "__main__":
    test_interval_overlaps()
    test_sanitizer_preserves_numbers()
    test_date_and_footnote_extraction()
    print("GATE 1 VERIFIED SUCCESSFULLY!")
