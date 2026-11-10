"""Run the frozen audit, challenge, and invariance checks against the actual submission."""

import json
from dataclasses import asdict
from pathlib import Path
from random import Random
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

from submission_cases import (
    EXTRA, LEGACY, PAIRS, audit_conflicts, auto, cd, cds, challenge,
    challenge_conflicts, mortgages, mutations,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
from contract import Page, Snapshot
from run_eval import load_snapshots, load_submission, to_answer
from scoring import _numbers, contains, score_conflicts, score_item, summarize

AUDIT_DATES = ("2031-02-10", "2031-04-10", "2031-06-10")
CHALLENGE_DATES = ("2034-03-10", "2034-06-10", "2034-09-10")


def system(stages, host, dates, reverse=False, evidence=None):
    engine = load_submission(ROOT / "solution")
    if evidence is not None:
        evidence[engine] = {}
    for i, pages in enumerate(stages):
        day = dates[i]
        snapshot = Snapshot(f"crawl-{i}", day, [
            Page(host + path, path, md, day) for path, md in (pages[::-1] if reverse else pages)
        ])
        if evidence is not None:
            evidence[engine].update(index_pages([snapshot]))
        engine.ingest(snapshot)
    return engine


def index_pages(snapshots):
    return {(s.snapshot_id, p.url): (s.taken_on, p.markdown) for s in snapshots for p in s.pages}


def evidence_errors(answer, pages, as_of):
    errors = []
    if answer["confident"] and not answer["sources"]:
        errors.append("confident without a source")
    for source in answer["sources"]:
        url = source["url"]
        snapshot = source["snapshot_id"]
        day, markdown = pages.get((snapshot, url), pages.get((snapshot, url.split("#", 1)[0]), (None, "")))
        if not day or (as_of and day > as_of) or not source["quote"] or source["quote"] not in markdown:
            errors.append(f"unverified source {snapshot} {url}")
    return errors


def grade_questions(cases, evidence, check_expected_evidence=False):
    results = []
    repeats = []
    source_failures = []
    for engine, item_id, question, as_of, required, forbidden, sources, group in cases:
        start = perf_counter()
        answer = to_answer(engine.answer(question, as_of))
        item = {
            "id": item_id, "question": question, "must_include": required or [],
            "must_not_include": forbidden, "expected_sources": sources, "group": group,
            "category": "should_refuse" if required is None else "general",
        }
        result = score_item(item, answer)
        result.update(answer=asdict(answer), latency_s=perf_counter() - start, error=None)
        if result["score"] < 1:
            print(f"  {item_id} {result['score']:.2f}: {question} -> {answer.text[:280]}")
        source_failures.extend(f"{item_id}: {err}" for err in evidence_errors(result["answer"], evidence[engine], as_of))
        if check_expected_evidence and required:
            for token in required:
                if not any(contains(source.quote or "", token) for source in answer.sources):
                    source_failures.append(f"{item_id}: expected claim missing from the cited evidence: {token}")
        results.append(result)
        repeats.append({"id": item_id, "text": to_answer(engine.answer(question, as_of)).text})
    return results, repeats, source_failures


def grade_conflicts(fixtures, host, dates):
    matched = submitted = expected = won = resolutions = 0
    unresolved_errors = []
    for name, stages, gold in fixtures:
        conflicts = system(stages, host, dates).conflicts()
        scored = score_conflicts(gold, conflicts)
        count = sum(bool(g["correct_value"]) for g in gold)
        matched += scored["matched"]
        submitted += len(conflicts)
        expected += len(gold)
        won += round((scored["resolution_accuracy"] or 0) * count)
        resolutions += count
        if gold and all(g["correct_value"] is None for g in gold) and (
            not conflicts or any(c.resolution is not None for c in conflicts)
        ):
            unresolved_errors.append(name)
        if scored["matched"] != len(gold) or len(conflicts) != len(gold) or (count and scored["resolution_accuracy"] != 1):
            print(f"  Conflict fixture {name}: {scored}")
    return {
        "gold": expected, "submitted": submitted, "matched": matched,
        "precision": matched / submitted if submitted else 0,
        "recall": matched / expected,
        "f1": 2 * matched / (expected + submitted),
        "resolution_accuracy": won / resolutions,
    }, unresolved_errors


def matrix():
    def check(engine, question, rate):
        answer = engine.answer(question)
        found = {value for unit, value in _numbers(answer.text) if unit == "%"}
        ok = (not answer.confident and not found) if rate is None else (
            answer.confident and found == {rate} and any(s.quote for s in answer.sources)
        )
        if not ok:
            print(f"  Matrix: {question} -> {answer.text[:220]}")
        return ok

    host = "https://matrix-cu.test"
    mortgage = {
        ("Purchase", 15): [(40000, 3.61), (120000, 3.75)],
        ("Purchase", 30): [(80000, 4.13), (220000, 4.37), (500000, 4.55)],
        ("Refinance", 15): [(70000, 4.01), (130000, 4.19)],
        ("Refinance", 30): [(100000, 4.59), (280000, 4.72)],
    }
    rows = [(f"{tx}: {term}-Year Fixed Conventional ${cap:,} or less", f"{rate - 0.10:.2f}%", f"{rate:.2f}%")
            for (tx, term), points in mortgage.items() for cap, rate in points]
    shuffled = rows.copy()
    Random(401).shuffle(shuffled)
    checks = []
    for layout in (rows, rows[::-1], shuffled):
        engine = system([[mortgages(layout)]], host, AUDIT_DATES)
        for (tx, term), points in mortgage.items():
            for i, (cap, rate) in enumerate(points):
                low = points[i - 1][0] if i else 0
                for amount in (f"${cap:,}", f"${low:,}.01", f"${cap / 1000:g}k"):
                    checks.append(check(engine, f"What is the {term}-year fixed {tx.lower()} mortgage APR on {amount}?", rate))
            checks.append(check(engine, f"What is the {term}-year fixed {tx.lower()} mortgage APR on ${points[-1][0]:,}.01?", None))

    terms = [
        (7, "seven", 3.15, 3.20, 3.65, 3.72), (11, "eleven", 3.18, 3.25, 3.71, 3.79),
        (19, "nineteen", 3.79, 3.85, 4.21, 4.29), (25, "twenty-five", 4.09, 4.13, 4.57, 4.65),
    ]
    rows = [row for term, _, div, apy, jumbo_div, jumbo_apy in terms for row in (
        (f"{term} Month", f"{div:.2f}%", f"{apy:.2f}%", "$725"),
        (f"{term} Month Fixed Jumbo Special", f"{jumbo_div:.2f}%", f"{jumbo_apy:.2f}%", "$83,000"),
    )]
    engine = system([[cds(rows)]], host, AUDIT_DATES)
    for term, word, div, apy, jumbo_div, jumbo_apy in terms:
        for label in (term, word):
            for kind, metric, rate in (("regular", "APY", apy), ("regular", "dividend rate", div),
                                       ("jumbo", "APY", jumbo_apy), ("jumbo", "dividend rate", jumbo_div)):
                checks.append(check(engine, f"What is the {metric} of the {label}-month {kind} certificate?", rate))

    rates = [(2024, 2027, 48, 4.33), (2024, 2027, 60, 4.51), (2028, 2031, 48, 5.12),
             (2028, 2031, 60, 5.26), (2028, 2031, 72, 5.48)]
    engine = system([[auto([(f"{lo}-{hi}", term, str(rate)) for lo, hi, term, rate in rates])]], host, AUDIT_DATES)
    for year in (2023, 2024, 2027, 2028, 2031, 2032):
        for term in (36, 48, 60, 72):
            rate = next((v for lo, hi, t, v in rates if t == term and lo <= year <= hi), None)
            checks.append(check(engine, f"What is the APR for a {term}-month auto loan on a {year} model-year vehicle?", rate))
    return sum(checks), len(checks)


def evidence_invariants():
    host = "https://invariance.test"
    question = "What is the APR for a 60-month auto loan on a 2033 model-year vehicle?"
    initial = [auto([("2032-35", 60, "4.21"), ("2032-35", 48, "3.50")])]
    changed = [auto([("2032-35", 60, "5.37"), ("2032-35", 48, "3.50")])]
    unrelated = [auto([("2032-35", 60, "5.37"), ("2032-35", 48, "8.25")])]
    original = system([initial], host, CHALLENGE_DATES).answer(question)
    updated = system([initial, changed], host, CHALLENGE_DATES)
    answer = updated.answer(question)
    same = system([initial, unrelated], host, CHALLENGE_DATES).answer(question)
    conflicts = updated.conflicts()
    tied = [[cd("3.21%", path="/rates/a"), cd("4.24%", path="/rates/b")]]
    tie_results = [system(tied, host, CHALLENGE_DATES, reverse=rev) for rev in (False, True)]
    retired = system([[cd("3.21%")], [cd("4.24%"), cd("2.79%", term=12),
                     ("/notice", "The 6-month certificate has been discontinued.")]], host, CHALLENGE_DATES)
    open_ended = system([[mortgages([
        ("Purchase: 30-Year Fixed Conventional $500,000 or less", "5.11%", "5.32%"),
        ("Purchase: 30-Year Fixed Conventional $500,001 or more", "5.66%", "5.89%"),
    ])]], host, CHALLENGE_DATES)
    tied_mortgage = [[mortgages([("Purchase: 30-Year Fixed Conventional $500,000 or less", "5.11%", "5.32%")]),
                     ("/rates/secondary", mortgages([("Purchase: 30-Year Fixed Conventional $500,000 or less", "5.76%", "5.98%")])[1])]]
    mortgage_tie_results = [system(tied_mortgage, host, CHALLENGE_DATES, reverse=rev) for rev in (False, True)]
    mortgage_question = "What is the 30-year purchase mortgage APR on $300,000?"
    return {
        "relevant change updates claim and quote": "4.21%" in original.text and "5.37%" in answer.text
        and "5.37%" in answer.sources[0].quote and original.sources != answer.sources,
        "unrelated tier leaves answer and quote alone": asdict(answer) == asdict(same),
        "answer and conflict agree": any("60-month" in c.topic and c.resolution == "5.37%" for c in conflicts),
        "page reversal leaves tie unresolved": all(
            not k.answer("What is the 6-month certificate APY?").confident
            and k.conflicts() and all(c.resolution is None for c in k.conflicts()) for k in tie_results
        ),
        "retirement suppresses a lingering row without affecting another term": (
            "3.21%" in retired.answer("What is the 6-month certificate APY?", CHALLENGE_DATES[0]).text
            and not retired.answer("What is the 6-month certificate APY?").confident
            and "2.79%" in retired.answer("What is the 12-month certificate APY?").text
            and retired.conflicts() and all(c.resolution is None for c in retired.conflicts())
        ),
        "open-ended mortgage tier overrides a lower finite ceiling": "5.89%" in open_ended.answer(
            "What is the 30-year purchase mortgage APR on $560,000?"
        ).text,
        "mortgage answer and conflict both leave a genuine tie unresolved": all(
            not engine.answer(mortgage_question).confident and engine.conflicts()
            and all(c.resolution is None for c in engine.conflicts()) for engine in mortgage_tie_results
        ),
    }


def official_checks(real_snapshots):
    with TemporaryDirectory(prefix="knowledge-submission-") as temp:
        detached = Path(temp) / "submission"
        shutil.copytree(ROOT / "solution", detached, ignore=shutil.ignore_patterns("__pycache__"))
        reports = {}
        source_failures = []
        for name, snaps, eval_path, options in (
            ("public", ROOT / "snapshots", ROOT / "eval" / "public_eval.jsonl", []),
            ("selftest", ROOT / "harness" / "selftest" / "snapshots", ROOT / "harness" / "selftest" / "eval.jsonl",
             ["--conflicts-gold", str(ROOT / "harness" / "selftest" / "conflicts_gold.json")]),
        ):
            out = Path(temp) / name
            completed = subprocess.run([
                sys.executable, "-I", "-B", "-X", "utf8", str(ROOT / "harness" / "run_eval.py"),
                "--submission", str(detached), "--snapshots", str(snaps), "--eval", str(eval_path), "--out", str(out),
                *options,
            ], cwd=temp, capture_output=True, text=True, encoding="utf-8")
            if completed.returncode:
                raise RuntimeError(completed.stdout + completed.stderr)
            reports[name] = json.loads((out / "report.json").read_text(encoding="utf-8"))
            snapshots = real_snapshots if name == "public" else load_snapshots(snaps)
            pages = index_pages(snapshots)
            cutoffs = {item["id"]: item.get("as_of") for line in eval_path.read_text(encoding="utf-8").splitlines()
                       if line.strip() for item in [json.loads(line)]}
            for line in (out / "per_question.jsonl").read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                source_failures.extend(f"{row['id']}: {err}"
                                       for err in evidence_errors(row["answer"], pages, cutoffs[row["id"]]))
    return reports, source_failures


def main():
    real = load_submission(ROOT / "solution")
    real_snapshots = load_snapshots(ROOT / "snapshots")
    evidence = {real: index_pages(real_snapshots)}
    for snapshot in real_snapshots:
        real.ingest(snapshot)
    cases = [(real, *case, (), None) for case in LEGACY]
    for i, (first, second, required) in enumerate(PAIRS, 1):
        cases.extend((real, f"P{i}-{j}", q, None, required, [], (), f"P{i}")
                     for j, q in enumerate((first, second), 1))
    cases.extend((real, *case, None) for case in EXTRA)
    worlds, audit_cases = mutations()
    audit_host = "https://audit-cu.test"
    engines = {name: system(stages, audit_host, AUDIT_DATES, evidence=evidence) for name, stages in worlds.items()}
    cases.extend((engines[world], item_id, q, day, must, forbidden, (),
                  "synthetic-feb" if item_id in {"M01", "M02", "M03"} else None)
                 for item_id, world, q, day, must, forbidden in audit_cases)
    assert len(cases) == 92
    print("Frozen audit")
    rows, repeats, audit_source_failures = grade_questions(cases, evidence)
    fixtures = audit_conflicts()
    assert len(fixtures) == 13
    audit_conflict_report, audit_unresolved = grade_conflicts(fixtures, audit_host, AUDIT_DATES)
    audit_report = summarize(rows, audit_conflict_report, repeats)

    print("Separate challenge")
    host = "https://challenge-cu.test"
    worlds, challenge_cases = challenge()
    engines = {name: system(stages, host, CHALLENGE_DATES, evidence=evidence) for name, stages in worlds.items()}
    cases = [(engines[world], f"H{i}-{j}", q, day, must, forbidden, (), f"H{i}")
             for i, (world, day, must, forbidden, first, second) in enumerate(challenge_cases, 1)
             for j, q in enumerate((first, second), 1)]
    assert len(cases) == 48
    challenge_rows, challenge_repeats, challenge_source_failures = grade_questions(cases, evidence, check_expected_evidence=True)
    fixtures = challenge_conflicts()
    assert len(fixtures) == 12
    challenge_conflict_report, challenge_unresolved = grade_conflicts(fixtures, host, CHALLENGE_DATES)
    challenge_report = summarize(challenge_rows, challenge_conflict_report, challenge_repeats)
    passed, total = matrix()
    invariants = evidence_invariants()
    stress_pass = sum(r["score"] == 1 for r in rows[:28])
    pair_pass = sum(
        a["correctness"] == b["correctness"] == 1 and _numbers(a["answer"]["text"]) == _numbers(b["answer"]["text"])
        for a, b in zip(rows[28:44:2], rows[29:44:2])
    )
    print("Detached organizer harness")
    official, official_source_failures = official_checks(real_snapshots)
    source_failures = audit_source_failures + challenge_source_failures + official_source_failures
    print(json.dumps({
        "audit": audit_report, "challenge": challenge_report, "stress": [stress_pass, 28],
        "paraphrase_pairs": [pair_pass, 8], "matrix": [passed, total],
        "detached": {"python": sys.version.split()[0], **official}, "source_failures": source_failures,
        "unresolved_failures": audit_unresolved + challenge_unresolved, "invariants": invariants,
    }, indent=2))
    return 0 if (
        audit_report["final_score"] >= 0.90 and challenge_report["final_score"] >= 0.90
        and all(report["stale_trap_hits"] == 0 and report["errors"] == 0 for report in (audit_report, challenge_report))
        and stress_pass == 28 and pair_pass == 8 and passed == total == 149
        and not (audit_unresolved or challenge_unresolved or source_failures) and all(invariants.values())
        and official["public"]["final_score"] >= 0.969 and official["selftest"]["final_score"] == 1.0
        and all(report["speed"]["p95_s"] <= 2 and not report["ingest_over_limit"] and report["errors"] == 0
                for report in official.values())
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
