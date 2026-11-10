# Submission notes

The submission implements `ingest`, `answer`, and `conflicts` from the organizer’s `KnowledgeSystem` contract. It uses the Python standard library and the supplied Markdown snapshots. The submission folder can be loaded independently of the rest of the repository.

## Evidence and time

- The parser stores each rate or fee with its product, metric, applicable qualifiers, snapshot, URL, and verbatim source passage. Mortgage ceiling tables are partitioned by term and transaction type before rates are selected. IRA subtypes, credit card products, and volunteer headcount and hours are compared separately.
- `as_of` sets the crawl cutoff. An explicit in-question date selects a snapshot at or before that date. Comparisons resolve each date independently. Rates must be effective and unexpired on the requested date. A later crawl cannot supply evidence for an earlier answer.
- The eligibility policy ranks qualified facts by authority, effective date, and crawl date, then counts distinct supporting pages for equal-ranked values. Equal support for different values produces an unresolved conflict. A scoped discontinuation notice can suppress a rate that still appears elsewhere in the same crawl.
- Published rates use the active crawl. For the replacement-fee case in the organizer self-test, an omitted fee page can supply a clearly labeled last-published observation. A surviving page that drops the fee or an explicit retirement notice suppresses that fallback. An unpublished relative reporting year is labeled unavailable, with historical context identified by its own year.

## Local submission gate

Run the following command with Python 3.11 from the repository root.

```powershell
python -B -X utf8 scratch/submission_gate.py
```

The gate applies the organizer’s scoring formulas to a frozen audit of 92 questions and 13 conflict fixtures and a separate set of 48 questions and 12 conflict fixtures. It requires at least 90% weighted overall on each set. It also checks the 28 stress questions, eight legacy paraphrase pairs, 149 generated boundary cases, and counterfactual evidence checks. Both organizer evaluations run against a detached copy of `solution/` using the original harness. The gate checks citations against the original Markdown and the supplied cutoffs, and it exits nonzero for a failed requirement.

| Evaluation | Weighted score | Additional checks |
|---|---:|---|
| Frozen audit | 100.00% | 92 questions, 13 conflict fixtures |
| Separate challenge | 100.00% | 48 questions across 24 paraphrase pairs, 12 conflict fixtures |
| Organizer public benchmark | 96.90% | 39 of 40 questions earned full mechanical credit, with zero stale traps or errors |
| Organizer synthetic self-test | 100.00% | 100% conflict F1 and resolution accuracy |

The public certificate-list question `dup-005` has no required tokens, so it receives zero mechanical credit even when the full schedule is returned. The public benchmark has no organizer conflict gold. The independent results measure the local fixtures, and the organizer’s hidden score is still unknown.

The earlier mutation and detached-import probes can also be run with `python -B -X utf8 scratch/verify_all.py`.
