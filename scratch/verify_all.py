import json
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "harness"))

from contract import Page, Snapshot
import solution


def test_detached_folder_import():
    print("\n--- Test 0: Detached Folder Import ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        sol_dir = Path(tmpdir) / "solution"
        shutil.copytree(repo_root / "solution", sol_dir)
        cmd = [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, r'" + str(sol_dir) + "'); import solution; ks = solution.build(); print('Detached build() OK')"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"Detached import failed:\n{res.stderr}"
        print(res.stdout.strip())
        print("[PASS] Detached folder import passed")


def test_probe_1_rate_change_and_removal():
    print("\n--- Probe 1: Rate Change & Removal ---")
    # Part A: Authoritative page has 9.87%, secondary calculator has 5.34% (expired)
    p_auth = Page(
        url="https://www.dupaco.com/personal/loans/auto-motorcycle-loans",
        title="Auto Loans",
        markdown="| Loan Program | Term | Annual Percentage Rate (As Low As) |\n| --- | --- | --- |\n| 2024-27 Model Year | 60 Months | 9.87% APR1 |\n\nRates are effective as of 09/25/2026.\n1Auto Loan Rates...",
        fetched_at="2026-09-28T00:00:00Z"
    )
    p_calc = Page(
        url="https://www.dupaco.com/calculators/auto-loan",
        title="Auto Calculator",
        markdown="| Loan Program | Term | Annual Percentage Rate |\n| --- | --- | --- |\n| 2023-26 Model Year | 60 Months | 5.34% APR |\n\nOffer expired: 01/01/2026.",
        fetched_at="2026-09-28T00:00:00Z"
    )
    s1 = Snapshot(snapshot_id="05_latest", taken_on="2026-09-28", pages=[p_auth, p_calc])
    ks = solution.build()
    ks.ingest(s1)

    a1 = ks.answer("What's the APR for a 60-month auto loan on a 2025 model-year vehicle?")
    print(f"Mutated auto answer: {a1.text}")
    assert "9.87%" in a1.text, f"Expected 9.87%, got {a1.text}"
    conflicts = ks.conflicts()
    auto_conflicts = [c for c in conflicts if "auto loan" in c.topic]
    assert len(auto_conflicts) > 0, "Expected auto loan conflict"
    assert auto_conflicts[0].resolution == "9.87%", f"Expected winner 9.87%, got {auto_conflicts[0].resolution}"

    # Part B: Authoritative page REMOVED, leaving only expired calculator offer (5.34%)
    s2 = Snapshot(snapshot_id="05_latest_removed", taken_on="2026-09-28", pages=[p_calc])
    ks2 = solution.build()
    ks2.ingest(s2)

    a2 = ks2.answer("What's the APR for a 60-month auto loan on a 2025 model-year vehicle?")
    print(f"Removed auth page answer: confident={a2.confident}, text={a2.text}")
    assert not a2.confident, f"Expected refusal (not confident), got {a2.text}"
    assert "5.34%" not in a2.text, f"Must not quote expired 5.34% as active, got {a2.text}"
    c2 = ks2.conflicts()
    if c2:
        for c in c2:
            assert c.resolution is None, f"Expired offer must have resolution=None, got {c.resolution}"
    print("[PASS] Probe 1: Rate Change & Removal passed")


def test_probe_2_dynamic_refusal_inversion():
    print("\n--- Probe 2: Dynamic Refusal Inversion ---")
    # Part A: Default without 9-month CD and without Galena Saturday schedule -> Refuse
    ks_refuse = solution.build()
    p_norm = Page(
        url="https://www.dupaco.com/rates",
        title="Rates",
        markdown="| Certificate Term | APY |\n| --- | --- |\n| 12-Month | 3.50% |\n| 24-Month | 3.75% |",
        fetched_at="2026-09-28T00:00:00Z"
    )
    p_contact = Page(
        url="https://www.dupaco.com/contact",
        title="Contact",
        markdown="Galena Branch: 11375 Oldenburg Ln., Galena, IL, 61036.\nLobby Hours: Mon-Fri 8:30 a.m. - 5 p.m. Saturday: Closed.",
        fetched_at="2026-09-28T00:00:00Z"
    )
    s_norm = Snapshot(snapshot_id="01", taken_on="2026-09-28", pages=[p_norm, p_contact])
    ks_refuse.ingest(s_norm)

    ans_cd = ks_refuse.answer("What is the rate on a 9-month certificate?")
    assert not ans_cd.confident, f"Expected refusal for 9-month certificate, got {ans_cd.text}"

    ans_sat = ks_refuse.answer("What are the Saturday hours for the Galena branch?")
    assert not ans_sat.confident, f"Expected refusal for Galena Saturday hours, got {ans_sat.text}"

    # Part B: Invert by publishing 9-month CD and Galena Saturday hours in a test snapshot
    ks_invert = solution.build()
    p_invert_cd = Page(
        url="https://www.dupaco.com/rates",
        title="Rates",
        markdown="| Certificate Term | APY |\n| --- | --- |\n| 9-Month Special | 4.65% |\n| 12-Month | 3.50% |",
        fetched_at="2026-09-28T00:00:00Z"
    )
    p_invert_contact = Page(
        url="https://www.dupaco.com/contact",
        title="Contact",
        markdown="Galena Branch: 11375 Oldenburg Ln., Galena, IL, 61036.\nLobby Hours: Mon-Fri 8:30 a.m. - 5 p.m.\nSaturday: 9 a.m. - 12 p.m.",
        fetched_at="2026-09-28T00:00:00Z"
    )
    s_invert = Snapshot(snapshot_id="02", taken_on="2026-09-28", pages=[p_invert_cd, p_invert_contact])
    ks_invert.ingest(s_invert)

    ans_cd2 = ks_invert.answer("What is the rate on a 9-month certificate?")
    print(f"Published 9m CD answer: {ans_cd2.text}")
    assert ans_cd2.confident, f"Expected confident answer for published 9-month CD, got {ans_cd2.text}"
    assert "4.65%" in ans_cd2.text, f"Expected 4.65%, got {ans_cd2.text}"

    ans_sat2 = ks_invert.answer("What are the Saturday hours for the Galena branch?")
    print(f"Published Galena Sat answer: {ans_sat2.text}")
    assert ans_sat2.confident, f"Expected confident answer for published Galena Saturday hours, got {ans_sat2.text}"
    assert "9 a.m." in ans_sat2.text, f"Expected 9 a.m. in answer, got {ans_sat2.text}"
    print("[PASS] Probe 2: Dynamic Refusal Inversion passed")


def test_probe_3_mortgage_row_shuffling():
    print("\n--- Probe 3: Mortgage Row Shuffling ---")
    p_shuffled = Page(
        url="https://www.dupaco.com/personal/loans/home-loans",
        title="Home Loans",
        markdown=(
            "| Program | Rate | Points | APR |\n"
            "| --- | --- | --- | --- |\n"
            "| Purchase: 30-Year Fixed Conventional $100,000 or less | 7.13% | 0 | 7.27% |\n"
            "| Purchase: 30-Year Fixed Conventional $832,750 or less | 7.25% | 0 | 7.29% |\n"
            "| Purchase: 30-Year Fixed Conventional $200,000 or less | 7.25% | 0 | 7.34% |\n"
            "| Purchase: 30-Year Fixed Conventional $300,000 or less | 7.25% | 0 | 7.32% |\n"
        ),
        fetched_at="2026-09-28T00:00:00Z"
    )
    s_shuff = Snapshot(snapshot_id="01", taken_on="2026-09-28", pages=[p_shuffled])
    ks = solution.build()
    ks.ingest(s_shuff)

    # $150,000 must match Tier 2 ($200k or less), which has 7.34% APR
    ans = ks.answer("What is the 30-year fixed purchase mortgage APR for a $150,000 loan?")
    print(f"Shuffled mortgage answer: {ans.text}")
    assert "7.34%" in ans.text, f"Expected 7.34% APR, got {ans.text}"
    assert "7.29%" not in ans.text, f"Must not match $832,750 tier (7.29%), got {ans.text}"
    assert ans.sources and "200,000 or less" in ans.sources[0].quote, f"Quote must cite $200k row, got {ans.sources[0].quote}"

    # Loan over $832,750 must refuse
    ans_jumbo = ks.answer("What is the 30-year fixed purchase mortgage APR for a $1,500,000 loan?")
    print(f"Jumbo mortgage answer: confident={ans_jumbo.confident}, text={ans_jumbo.text}")
    assert not ans_jumbo.confident, f"Expected refusal for loan exceeding $832,750, got {ans_jumbo.text}"
    print("[PASS] Probe 3: Mortgage Row Shuffling passed")


def test_probe_4_range_endpoint_mutation():
    print("\n--- Probe 4: Range Endpoint Mutation ---")
    p_orig = Page(
        url="https://www.dupaco.com/personal/credit-cards",
        title="Credit Cards",
        markdown="### Platinum Visa\n- APR2: 12.50%–19.50%",
        fetched_at="2026-05-12T00:00:00Z"
    )
    p_mut = Page(
        url="https://www.dupaco.com/personal/credit-cards",
        title="Credit Cards",
        markdown="### Platinum Visa\n- APR2: 12.50%–21.50%",
        fetched_at="2026-09-28T00:00:00Z"
    )
    s1 = Snapshot(snapshot_id="01", taken_on="2026-05-12", pages=[p_orig])
    s2 = Snapshot(snapshot_id="05", taken_on="2026-09-28", pages=[p_mut])

    ks = solution.build()
    ks.ingest(s1)
    ks.ingest(s2)

    conflicts = ks.conflicts()
    card_conflicts = [c for c in conflicts if "platinum visa" in c.topic or "credit card" in c.topic]
    assert len(card_conflicts) > 0, "Must detect range conflict between 12.50%-19.50% and 12.50%-21.50%"
    c = card_conflicts[0]
    print(f"Detected conflict topic: {c.topic}, values: {c.values}")
    assert any("19.50%" in v for v in c.values) and any("21.50%" in v for v in c.values), f"Expected both ranges, got {c.values}"
    assert c.resolution is not None, "Expected valid resolution"
    print("[PASS] Probe 4: Range Endpoint Mutation passed")


def test_probe_5_unrelated_snapshot_isolation():
    print("\n--- Probe 5: Unrelated Snapshot Isolation ---")
    p_unrelated = Page(
        url="https://example.org/about",
        title="About Us",
        markdown="# Welcome to Prairie Nature Sanctuary\nWe protect wetlands and prairie grasslands in the Midwest. Come visit our walking trails from sunrise to sunset.",
        fetched_at="2026-09-28T00:00:00Z"
    )
    s_unrelated = Snapshot(snapshot_id="01_unrelated", taken_on="2026-09-28", pages=[p_unrelated])
    ks = solution.build()
    ks.ingest(s_unrelated)

    queries = [
        "What is the APY on the 17-month special certificate?",
        "What are the mortgage rates right now?",
        "Can you tell me the interest rate for a 30-year mortgage?",
        "What credit cards do you offer and what are their APRs?",
        "What is the standard purchase APR on the Dupaco Platinum Visa?",
        "How many branches does Dupaco have?",
        "What is Dupaco's routing number?",
        "How many Dupaco employees used Volunteer Time Off last year?",
        "What recognition has Dupaco received from Forbes?",
        "What is the APR on a 60-month auto loan on a 2025 model-year vehicle?",
        "What is the lost debit card replacement fee?",
    ]

    for q in queries:
        ans = ks.answer(q)
        print(f"Query: '{q[:40]}...' -> confident={ans.confident}, text='{ans.text}'")
        assert not ans.confident, f"Expected confident=False for unrelated snapshot on query '{q}', got text='{ans.text}'"
        for forbidden in ("12.50%", "19.50%", "4.10%", "4.20%", "7.13%", "273974581", "22 branch", "294 employees"):
            assert forbidden not in ans.text, f"Fabricated literal '{forbidden}' found in answer: '{ans.text}'"

    print("[PASS] Probe 5: Unrelated Snapshot Isolation passed")


def test_general_mutations():
    print("\n--- Test Additional Mutations & Policies ---")
    # Selftest CD rate mutation to 8.25%
    ks2 = solution.build()
    p_cd1 = Page(url="https://example.com/certificates", title="Certificates", markdown="6-month certificate APY: 2.95%", fetched_at="2026-01-05T00:00:00Z")
    s_cd1 = Snapshot(snapshot_id="snap1", taken_on="2026-01-05", pages=[p_cd1])
    p_cd2 = Page(url="https://example.com/certificates", title="Certificates", markdown="6-month certificate APY: 8.25%", fetched_at="2026-03-01T00:00:00Z")
    s_cd2 = Snapshot(snapshot_id="snap2", taken_on="2026-03-01", pages=[p_cd2])
    ks2.ingest(s_cd1)
    ks2.ingest(s_cd2)
    ans2 = ks2.answer("What is the 6-month certificate APY?", as_of="2026-03-01")
    assert "8.25%" in ans2.text, f"Expected 8.25%, got {ans2.text}"
    conflicts = ks2.conflicts()
    assert any("8.25%" in c.values for c in conflicts), "Mutated CD rate must appear in conflicts"
    print("[PASS] CD rate mutation to 8.25% passed")

    # Lost debit card fee mutation to $9.99
    ks3 = solution.build()
    p_fee = Page(url="https://example.com/fees", title="Fees", markdown="Lost debit card replacement fee: $9.99", fetched_at="2026-01-05T00:00:00Z")
    s_fee = Snapshot(snapshot_id="snap_fee", taken_on="2026-01-05", pages=[p_fee])
    ks3.ingest(s_fee)
    ans3 = ks3.answer("What is the fee to replace a lost debit card?")
    assert "$9.99" in ans3.text, f"Expected $9.99, got {ans3.text}"
    print("[PASS] Debit card fee mutation to $9.99 passed")

    # Last-known policy on omitted page
    ks4 = solution.build()
    p_f1 = Page(url="https://example.com/fees", title="Fees", markdown="Lost debit card replacement fee: $2.50", fetched_at="2026-01-05T00:00:00Z")
    s_f1 = Snapshot(snapshot_id="01_baseline", taken_on="2026-01-05", pages=[p_f1])
    p_other = Page(url="https://example.com/other", title="Other", markdown="Other info", fetched_at="2026-03-01T00:00:00Z")
    s_f2 = Snapshot(snapshot_id="02_update", taken_on="2026-03-01", pages=[p_other])
    ks4.ingest(s_f1)
    ks4.ingest(s_f2)
    ans4 = ks4.answer("What is the lost debit card replacement fee?", as_of="2026-03-01")
    assert "$2.50" in ans4.text, f"Expected $2.50 via last-known policy, got {ans4.text}"
    assert any(s.snapshot_id == "01_baseline" for s in ans4.sources), "Source must cite original baseline snapshot"
    print("[PASS] Last-known policy on omitted page passed")

    # Renamed snapshot IDs and hostnames
    ks5 = solution.build()
    p_renamed = Page(url="https://community-union.net/rates", title="Rates", markdown="6-month certificate APY: 4.50%", fetched_at="2026-05-01T00:00:00Z")
    s_renamed = Snapshot(snapshot_id="custom_snap_999", taken_on="2026-05-01", pages=[p_renamed])
    ks5.ingest(s_renamed)
    ans5 = ks5.answer("What is the 6-month certificate APY?")
    assert "4.50%" in ans5.text
    assert ans5.sources[0].snapshot_id == "custom_snap_999"
    assert "community-union.net" in ans5.sources[0].url
    print("[PASS] Dynamic metadata passed")

    # Empty ledger safety
    ks_empty = solution.build()
    assert ks_empty.conflicts() == []
    ans_empty = ks_empty.answer("What is the certificate rate?")
    assert not ans_empty.confident
    print("[PASS] Empty ledger safety passed")


def test_judge_reproductions():
    print("\n--- Test 6: End-to-End Judge Report Reproductions ---")
    # 1. Single mortgage row reproduction ($550,000 or less at 4.10% interest / 4.25% APR)
    ks_mort = solution.build()
    p_single_mort = Page(
        url="https://www.dupaco.com/personal/loans/home-loans",
        title="Home Loans",
        markdown=(
            "| Program | Rate | Points | APR |\n"
            "| --- | --- | --- | --- |\n"
            "| Purchase: 30-Year Fixed Conventional $550,000 or less | 4.10% | 0 | 4.25% |\n"
        ),
        fetched_at="2026-09-28T00:00:00Z",
    )
    ks_mort.ingest(Snapshot(snapshot_id="s_mort", taken_on="2026-09-28", pages=[p_single_mort]))
    a_curr = ks_mort.answer("What are the mortgage rates right now?")
    print(f"Single-row mortgage answer: {a_curr.text}")
    assert "4.10%" in a_curr.text and "4.25%" in a_curr.text, f"Expected 4.10% / 4.25%, got {a_curr.text}"
    for forbidden in ("7.13%", "6.75%", "$100k", "$100,000"):
        assert forbidden not in a_curr.text, f"Hardcoded mortgage claim '{forbidden}' leaked into '{a_curr.text}'"

    a_cap = ks_mort.answer("What is the interest rate for a $600,000 mortgage?")
    print(f"Single-row $600k ceiling refusal: confident={a_cap.confident}, text={a_cap.text}")
    assert not a_cap.confident, "Expected refusal for $600,000 above $550,000 ceiling"
    assert "$550,000" not in a_cap.text, f"The refusal must not introduce the ceiling: {a_cap.text}"
    assert any("$550,000" in source.quote for source in a_cap.sources), "The refusal must cite the actual ceiling"

    # 2. Qualifier strictness: $150k shorthand, 2099 model year, 15-month CD, regular-only / dividend-rate CD
    p_certs = Page(
        url="https://www.dupaco.com/rates",
        title="Rates",
        markdown=(
            "| Certificate Term | Dividend Rate | APY | Minimum Balance |\n"
            "| --- | --- | --- | --- |\n"
            "| 17 Month Fixed Certificate Special | 4.06% | 4.10% | $1,000.00 |\n"
            "| 17 Month Fixed Jumbo Certificate Special | 4.15% | 4.20% | $100,000.00 |\n"
            "\n"
            "| Loan Program | Term | Annual Percentage Rate |\n"
            "| --- | --- | --- |\n"
            "| 2024-27 Model Year | 60 Months | 5.39% APR |\n"
        ),
        fetched_at="2026-09-28T00:00:00Z",
    )
    ks_qual = solution.build()
    ks_qual.ingest(Snapshot(snapshot_id="s_qual", taken_on="2026-09-28", pages=[p_certs]))

    a_15m = ks_qual.answer("What is the rate on a 15-month certificate?")
    assert not a_15m.confident, f"Expected refusal for 15-month certificate, got {a_15m.text}"

    a_reg = ks_qual.answer("What is the APY on the 17-month regular certificate?")
    assert a_reg.confident and "4.10%" in a_reg.text and "4.20%" not in a_reg.text, f"Expected regular-only 4.10%, got {a_reg.text}"

    a_div = ks_qual.answer("What is the dividend rate on the 17-month regular certificate?")
    assert a_div.confident and "4.06%" in a_div.text and "4.10%" not in a_div.text, f"Expected dividend rate 4.06%, got {a_div.text}"

    a_2099 = ks_qual.answer("What is the APR for a 60-month auto loan on a 2099 model-year vehicle?")
    assert not a_2099.confident, f"Expected refusal for 2099 vehicle model year, got {a_2099.text}"

    # 3. Elementary interval splitting for model-year tiers in conflicts()
    ks_int = solution.build()
    p_int_rates = Page(
        url="https://www.dupaco.com/rates",
        title="Rates",
        markdown=(
            "| Loan Program | Term | Annual Percentage Rate |\n"
            "| --- | --- | --- |\n"
            "| 2021-23 Model Year | 60 Months | 5.89% APR |\n"
            "| 2024-27 Model Year | 60 Months | 5.39% APR |\n"
        ),
        fetched_at="2026-09-28T00:00:00Z",
    )
    p_int_guide = Page(
        url="https://www.dupaco.com/auto-guide",
        title="Auto Guide",
        markdown=(
            "| Loan Program | Term | Annual Percentage Rate |\n"
            "| --- | --- | --- |\n"
            "| 2023-26 Model Year | 60 Months | 5.34% APR |\n"
        ),
        fetched_at="2026-09-28T00:00:00Z",
    )
    ks_int.ingest(Snapshot(snapshot_id="s_int", taken_on="2026-09-28", pages=[p_int_rates, p_int_guide]))
    int_conflicts = ks_int.conflicts()
    for c in int_conflicts:
        assert not ("5.89%" in c.values and "5.39%" in c.values), f"Disjoint model-year tiers merged into one conflict: {c.topic} -> {c.values}"

    # 4. Removed older URL supersession & fee retirement vs omitted page
    ks_sup = solution.build()
    p_old_promo = Page(
        url="https://www.dupaco.com/legacy-auto-promo",
        title="Auto Promo",
        markdown="| Loan Program | Term | Annual Percentage Rate |\n| --- | --- | --- |\n| 2024-27 Model Year | 60 Months | 9.99% APR |\n\nEffective 09/30/2026.",
        fetched_at="2026-05-12T00:00:00Z",
    )
    p_curr_rates = Page(
        url="https://www.dupaco.com/rates",
        title="Rates",
        markdown="| Loan Program | Term | Annual Percentage Rate |\n| --- | --- | --- |\n| 2024-27 Model Year | 60 Months | 5.39% APR |\n\nEffective 09/25/2026.",
        fetched_at="2026-09-28T00:00:00Z",
    )
    ks_sup.ingest(Snapshot(snapshot_id="01_may", taken_on="2026-05-12", pages=[p_old_promo]))
    ks_sup.ingest(Snapshot(snapshot_id="02_sep", taken_on="2026-09-28", pages=[p_curr_rates]))
    a_sup = ks_sup.answer("What is the 60-month auto loan APR for a 2025 vehicle?")
    c_sup = [c for c in ks_sup.conflicts() if "auto loan" in c.topic]
    assert "5.39%" in a_sup.text, f"Expected active snapshot 5.39%, got {a_sup.text}"
    assert c_sup and c_sup[0].resolution == "5.39%", f"Expected conflict winner 5.39%, got {c_sup[0].resolution if c_sup else None}"

    # Surviving fee page that retires a fee must refuse (not resurrect from older snapshot)
    ks_retire = solution.build()
    p_fee1 = Page(url="https://example.com/fees", title="Fees", markdown="Lost debit card replacement fee: $2.50\nOverdraft fee: $27.00", fetched_at="2026-01-05T00:00:00Z")
    p_fee2 = Page(url="https://example.com/fees", title="Fees", markdown="Overdraft fee: $27.00", fetched_at="2026-03-01T00:00:00Z")
    ks_retire.ingest(Snapshot(snapshot_id="01", taken_on="2026-01-05", pages=[p_fee1]))
    ks_retire.ingest(Snapshot(snapshot_id="02", taken_on="2026-03-01", pages=[p_fee2]))
    a_ret = ks_retire.answer("What is the lost debit card replacement fee?", as_of="2026-03-01")
    assert not a_ret.confident, f"Expected refusal when surviving fee page removes fee, got {a_ret.text}"
    print("[PASS] End-to-End Judge Report Reproductions passed")


if __name__ == "__main__":
    test_detached_folder_import()
    test_probe_1_rate_change_and_removal()
    test_probe_2_dynamic_refusal_inversion()
    test_probe_3_mortgage_row_shuffling()
    test_probe_4_range_endpoint_mutation()
    test_probe_5_unrelated_snapshot_isolation()
    test_general_mutations()
    test_judge_reproductions()
    print("\n==========================================")
    print("ALL 6 PROBE SUITES & JUDGE REPRODUCTIONS PASSED!")
    print("==========================================")
