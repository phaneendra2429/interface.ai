# Knowledge System Architecture & Technical Design (Final Approved Specification — v5.0)

> **Single Source of Truth for Implementation (SDLC Phases 1–4)**  
> This document consolidates the complete requirements analysis, contract invariants, re-audit resolutions, concrete extraction/conflict algorithms, shared eligibility policy, and the mandatory 5-probe paired mutation acceptance gate. Every implementation decision in `solution/` strictly adheres to this specification.

---

## 1. Non-Negotiable Contract & Scope Invariants

1. **Immutable Harness & Evaluation Files**:
   - Do **not** modify `harness/contract.py`, `harness/scoring.py`, `harness/run_eval.py`, `harness/selftest/*`, or `eval/public_eval.jsonl`.
2. **Submission Entrypoint & Detached Import Safety (`solution/`)**:
   - `solution/solution.py` defines `def build() -> KnowledgeSystem` returning an object with the exact three methods:
     - `ingest(snapshot: Snapshot) -> None`
     - `answer(question: str, as_of: Optional[str] = None) -> Answer`
     - `conflicts() -> list[Conflict]`
   - **Detached Grading Compatibility**: Every module in `solution/` (`solution.py`, `models.py`, `sanitizer.py`, `policy.py`, `parser.py`, `ledger.py`, `resolver.py`, `conflict_engine.py`) imports `contract` safely without calling `next(d / "harness" ...)` unconditionally. If `contract` is already in `sys.modules` or importable on `sys.path`, use it directly; otherwise search ancestors with `next(..., None)` and fall back to protocol-compatible dataclasses so loading `solution/` from an external directory outside the kit never raises `StopIteration` or `NameError`.
3. **100% Evidence-Driven Execution (Zero Hardcoded Answers or Conflicts)**:
   - No numeric values, dates, URLs, hostnames (e.g., `example-cu.test`), snapshot IDs, or answer/conflict strings may be hardcoded into `resolver.py` or `conflict_engine.py`.
   - Every returned value, condition, duration, conflict, winner, and `Source(url, snapshot_id, quote)` must be computed dynamically from the ingested `Snapshot` pages.
   - If an unrelated snapshot with zero financial data is ingested, the system must return `confident=False` for credit cards, mortgage rates, certificate rates, and branch details.
4. **Operational Bounds**:
   - **No Live Network Calls**: Rely exclusively on ingested `Snapshot` objects.
   - **Ingest Budget**: $\le 30\text{ minutes}$ total across all snapshots.
   - **Query Timeout & Speed Target**: Hard limit $60\text{s}$ per `answer()` call; target $p_{95} \le 2.0\text{s}$ for a $1.0$ Speed score.

---

## 2. Shared Ingestion, Eligibility, and Decision Policy (`policy.py`)

To eliminate divergence between `answer()` and `conflicts()`, both subsystems share a single **`EligibilityEngine`**:

```python
class EligibilityEngine:
    """Unified policy shared across answer() and conflicts()."""
    @staticmethod
    def is_eligible(fact: ExtractedFact, target_date: str, cutoff_date: str) -> bool:
        # Crawl visibility: must have been observed on or before cutoff
        if fact.taken_on > cutoff_date:
            return False
        # Offer expiry: if explicitly expired before target date, strictly ineligible as active
        if fact.expiry_date and fact.expiry_date < target_date:
            return False
        return True

    @staticmethod
    def rank_key(fact: ExtractedFact):
        # 1. Authority score (primary rate sheet > directory > newsroom > promo LP > calculator)
        # 2. In-text effective date (most recent takes precedence)
        # 3. Snapshot taken_on date
        return (
            fact.authority_score,
            fact.effective_date or "0000-00-00",
            fact.taken_on,
        )
```

- When an authoritative page is removed and only an expired calculator/promo remains:
  - Both `answer()` and `conflicts()` mark the expired promo as ineligible.
  - `answer()` refuses to quote the expired rate as active (`confident=False`).
  - `conflicts()` records the historical disagreement with `resolution = None`.

---

## 3. Core Semantic & Algorithmic Rules

### Rule 1: Canonical `FactIdentity` & Typed Values (`models.py`)

Assertions are comparable if and only if they share the same fully qualified identity:

```python
@dataclass(frozen=True)
class TypedValue:
    is_range: bool
    low: float
    high: Optional[float] = None
    unit: str = "%"              # "%", "$", ""
    raw_str: str = ""

    def equals(self, other: TypedValue) -> bool:
        if self.unit != other.unit or self.is_range != other.is_range:
            return False
        if not self.is_range:
            return abs(self.low - other.low) < 1e-4
        return abs(self.low - other.low) < 1e-4 and abs((self.high or 0.0) - (other.high or 0.0)) < 1e-4

@dataclass(frozen=True)
class IntervalBound:
    low: Optional[float] = None
    high: Optional[float] = None
    low_inclusive: bool = True
    high_inclusive: bool = True
    unit: str = ""              # "$", "year", "month"
    raw_label: str = field(default="", compare=False, hash=False)

    def contains(self, val: float) -> bool: ...
    def overlaps(self, other: IntervalBound) -> bool: ...

@dataclass(frozen=True)
class FactIdentity:
    entity: str                 # "mortgage", "auto_loan", "certificate", "checking", "savings", "credit_card", "personal_loan", "debit_card"
    product_name: str           # "platinum_visa", "rewards_visa", "business_platinum", "share_savings", "bright_savings", "1st_rate_checking"
    attribute: str              # "apr", "apy", "dividend_rate", "interest_rate", "replacement_fee", "branch_count"
    account_category: str = "personal"  # "personal", "business", "health_savings", "ira"
    is_jumbo: bool = False
    is_special: bool = False
    is_intro: bool = False
    transaction_type: str = ""  # "purchase", "refinance", ""
    term_months: Optional[int] = None
    tier_interval: Optional[IntervalBound] = None
    reporting_period: Optional[str] = None
```

### Rule 2: Table Partitioning & Descending Ceiling Inversion Fix (`parser.py`)

1. **Mortgage Table Ceiling Sorting**:
   - Within each loan program and transaction type, collect all ceiling rows (`$X or less`).
   - Extract raw ceiling values: e.g. `[832750, 300000, 200000, 100000]`.
   - Sort amounts in ascending order: `[100000, 200000, 300000, 832750]`.
   - Partition into disjoint half-open intervals:
     - Tier 1: `(0, 100000]` $\to$ `7.13%` rate / `7.27% APR`
     - Tier 2: `(100000, 200000]` $\to$ `7.25%` rate / `7.34% APR` (matches `$150,000` loan!)
     - Tier 3: `(200000, 300000]` $\to$ `7.25%` rate / `7.32% APR`
     - Tier 4: `(300000, 832750]` $\to$ `7.25%` rate / `7.29% APR`
   - **Ceiling Overflow**: If a query asks for an amount greater than the highest published ceiling (e.g. `$1.5M`), the resolver refuses (`confident=False`), stating that conforming tables cap at `$832,750`.
2. **Footnote Linkage**:
   - Cell footnote markers (`APR1`, `2`) are linked to their corresponding footnote definition.
   - For `Platinum Visa`, the extracted fact includes `footnote_text` documenting the 90-day transfer window and 6-month intro duration.

### Rule 3: 4-Clock Policy & Date Normalization (`resolver.py`)

1. **Crawl Visibility Ceiling (`cutoff_date`)**:
   - `cutoff_date = as_of if as_of is not None else latest_snapshot.taken_on`.
   - If `cutoff_date < earliest_snapshot_date` $\to$ Refuse (`confident=False`).
2. **Requested Target Date (`requested_date`)**:
   - **Month-Only Normalization**: Expressions such as `"as of May 2026"` resolve to the **last day of the month** (`2026-05-31`), ensuring May crawls (`2026-05-12`, `2026-05-19`) are eligible.
   - **Future-Relative-to-Cutoff**: If `as_of` is set and `requested_date > as_of`, refuse immediately (`confident=False`).
   - **Historical Two-Date Comparisons**: Evaluate date $d_1$ and date $d_2$ independently under the `as_of` ceiling. If either date exceeds `as_of`, refuse the future date rather than peeking past the cutoff.

### Rule 4: Dynamic Conflict Engine (`conflict_engine.py`)

1. **Product-Level Comparison**:
   - Compares facts grouped by `(entity, product_name, attribute, account_category, transaction_type, is_jumbo, is_special, is_intro, term_months, reporting_period)`.
   - Distinct cards (`Platinum Visa` vs `Rewards Visa`) are separated by `product_name` and never merged into conflicts.
2. **Pairwise Shared-Segment Overlaps**:
   - Model-year intervals are compared pairwise on their direct intersection. Disjoint current bands (`2021–23` and `2024–27`) are never linked via an older transitive bridge (`2023–26`).
3. **Typed Value Comparison**:
   - Compares `TypedValue` objects. Formatting differences (`3.5%` vs `3.50%`) are recognized as equal. Range changes (`12.50%–19.50%` vs `12.50%–21.00%`) are detected without float-conversion crashes.

---

## 4. Mandatory 5-Probe Paired Mutation Acceptance Suite

Before accepting the implementation, `scratch/verify_all.py` must execute and pass the five paired counterfactual tests:

| Test | Mutation / Action | Required System Behavior |
| :--- | :--- | :--- |
| **1. Rate Change & Removal** | Change published auto APR to `9.87%`, then remove the authoritative page leaving only an expired calculator offer (`5.34%`). | When changed, `answer()` and `conflicts()` select `9.87%`. When removed, neither quotes the expired `5.34%` as active. |
| **2. Dynamic Refusal Inversion** | Publish a 9-month CD or a Saturday branch schedule in a test snapshot. | The prior refusal turns into an accurate, sourced answer citing the newly published page. |
| **3. Mortgage Row Shuffling** | Invert or randomize rows in a mortgage table. | `$150,000` still selects the `$200k or less` tier (`7.34%` purchase) and cites that exact row. |
| **4. APR Range Endpoint Mutation** | Mutate upper bound of `Platinum Visa` from `19.50%` to `21.50%`. | Engine detects conflict between `12.50%–19.50%` and `12.50%–21.50%` without crashing or treating it as equal. |
| **5. Unrelated Snapshot Isolation** | Ingest a snapshot with a single page containing no financial data. | All financial and branch queries return `confident=False` with zero invented facts. |
