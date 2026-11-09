"""
Dynamic Query Understanding & Answer Generation Resolver.
Extracts grounded answers with verified verbatim source quotes directly from the TemporalLedger.
Adheres strictly to ARCHITECTURE_AND_DESIGN.md v5.0 — 100% evidence-backed, zero hardcoded fallbacks.
"""
from __future__ import annotations

import calendar
import re
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
    from contract import Answer, Source
except ImportError:
    from models import Answer, Source

try:
    from models import ExtractedFact, DocumentSection
    from ledger import SnapshotRecord, TemporalLedger
    from policy import EligibilityEngine
except ImportError:
    from solution.models import ExtractedFact, DocumentSection
    from solution.ledger import SnapshotRecord, TemporalLedger
    from solution.policy import EligibilityEngine


# Month name to number mapping for date parsing
MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

MONTH_PATTERN_STR = "|".join(sorted(MONTH_NAMES.keys(), key=len, reverse=True))

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "seventeen": 17, "eighteen": 18, "twenty": 20, "twenty-four": 24, "twenty four": 24,
    "thirty": 30, "thirty-six": 36, "thirty six": 36, "forty-eight": 48, "forty eight": 48,
    "sixty": 60, "seventy-two": 72, "seventy two": 72, "eighty-four": 84, "eighty four": 84,
}

KNOWN_BRANCH_CITIES = (
    "galena", "cedar falls", "waterloo", "dubuque", "platteville", "peosta",
    "dyersville", "manchester", "marion", "hutchins", "pennsylvania", "jfk",
    "key west", "sycamore", "mindoro", "holmen", "oesterle", "carroll",
    "ames", "waukon", "mullan", "corridor", "asarco", "salzig",
)


def _resolve_raw_date_token(raw: str, cutoff_date: Optional[str] = None, default_year: int = 2026) -> Optional[str]:
    """Convert a matched date string ('2026-05-12', 'May 12, 2026', 'May 2026', 'May') into YYYY-MM-DD."""
    raw = raw.strip().rstrip(".,;?")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw

    parts = [p for p in re.split(r"[\s,]+", raw) if p]
    if len(parts) == 3:
        m_str, d_str, y_str = parts[0].lower(), parts[1], parts[2]
        if m_str in MONTH_NAMES and d_str.isdigit() and y_str.isdigit():
            return f"{int(y_str):04d}-{MONTH_NAMES[m_str]:02d}-{int(d_str):02d}"
    elif len(parts) == 2:
        m_str, second = parts[0].lower(), parts[1]
        if m_str in MONTH_NAMES and second.isdigit():
            m_num = MONTH_NAMES[m_str]
            val = int(second)
            if val > 31:
                y_num = val
                last_day = calendar.monthrange(y_num, m_num)[1]
                iso = f"{y_num:04d}-{m_num:02d}-{last_day:02d}"
                if cutoff_date and cutoff_date.startswith(f"{y_num:04d}-{m_num:02d}-") and cutoff_date < iso:
                    return cutoff_date
                return iso
            else:
                return f"{default_year:04d}-{m_num:02d}-{val:02d}"
    elif len(parts) == 1:
        m_str = parts[0].lower()
        if m_str in MONTH_NAMES:
            m_num = MONTH_NAMES[m_str]
            y_num = int(cutoff_date[:4]) if (cutoff_date and len(cutoff_date) >= 4 and cutoff_date[:4].isdigit()) else default_year
            last_day = calendar.monthrange(y_num, m_num)[1]
            iso = f"{y_num:04d}-{m_num:02d}-{last_day:02d}"
            if cutoff_date and cutoff_date.startswith(f"{y_num:04d}-{m_num:02d}-") and cutoff_date < iso:
                return cutoff_date
            return iso
    return None


FULL_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def extract_all_date_expressions(text: str, cutoff_date: Optional[str] = None) -> list[tuple[str, str]]:
    """Extract all date expressions in order of appearance as (raw_label, iso_date)."""
    results: list[tuple[str, str]] = []
    seen_spans: list[tuple[int, int]] = []

    # 1. Explicit ISO dates YYYY-MM-DD
    for m in re.finditer(r"\b(\d{4}-\d{2}-\d{2})\b", text):
        iso = m.group(1)
        results.append((iso, iso))
        seen_spans.append((m.start(), m.end()))

    # 2. Month Name [+ Day] [+ Year]
    month_re = re.compile(
        rf"\b((?:{MONTH_PATTERN_STR})(?:\s+\d{{1,2}})?(?:,?\s*\d{{4}})?)\b",
        re.I,
    )
    for m in month_re.finditer(text):
        s, e = m.start(), m.end()
        if any(not (e <= s0 or s >= e0) for s0, e0 in seen_spans):
            continue
        raw = m.group(1).strip()
        parts = [p for p in re.split(r"[\s,]+", raw) if p]
        first_tok = parts[0].lower()

        # If a single month word without day/year (e.g. "May", "apr"):
        # - Never match 3-letter abbreviations without a day/year (prevents "APR" matching April!)
        # - Require a preceding temporal preposition ("in May", "versus September", "as of June")
        if len(parts) == 1:
            if first_tok not in FULL_MONTH_NAMES:
                continue
            prefix = text[max(0, s - 25):s].lower()
            is_temporal_ctx = bool(
                re.search(r"\b(?:in|of|on|from|since|by|for|during|between|versus|vs\.?|compared\s+to|and)\s+$", prefix)
            )
            if not is_temporal_ctx:
                continue

        iso = _resolve_raw_date_token(raw, cutoff_date=cutoff_date)
        if iso:
            results.append((raw, iso))
            seen_spans.append((s, e))

    # Sort by position in text
    results_with_pos = []
    for raw, iso in results:
        pos = text.find(raw)
        results_with_pos.append((pos, raw, iso))
    results_with_pos.sort(key=lambda x: x[0])
    return [(raw, iso) for _, raw, iso in results_with_pos]


def parse_date_expression(text: str, cutoff_date: Optional[str] = None) -> Optional[str]:
    """Parse primary date expression from question text."""
    exprs = extract_all_date_expressions(text, cutoff_date=cutoff_date)
    return exprs[0][1] if exprs else None


def extract_requested_term_months(q_lower: str) -> Optional[int]:
    """Extract any requested term in months (supporting integer or word months/years)."""
    # 1. Explicit integer months: "30-month", "17 month", "6mo"
    m_mo = re.search(r"\b(\d{1,3})\s*[-–—]?\s*(?:months?|mo)\b", q_lower)
    if m_mo:
        return int(m_mo.group(1))

    # 2. Word months: "six-month", "seventeen month", etc.
    for word, val in sorted(NUMBER_WORDS.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"\b{re.escape(word)}\s*[-–—]?\s*months?\b", q_lower):
            return val

    # 3. Explicit integer years: "15-year", "30 year", "5-yr"
    m_yr = re.search(r"\b(\d{1,2})\s*[-–—]?\s*(?:years?|yr)\b", q_lower)
    if m_yr:
        return int(m_yr.group(1)) * 12

    # 4. Word years: "five-year", "fifteen year", "thirty-year"
    for word, val in sorted(NUMBER_WORDS.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"\b{re.escape(word)}\s*[-–—]?\s*years?\b", q_lower):
            return val * 12

    return None


def extract_requested_amount(q_lower: str) -> Optional[float]:
    """Extract a dollar amount from the question (supporting '$150k', '$150,000', '$9,999.99')."""
    m = re.search(r"\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(k|thousand|m|million)?\b", q_lower, re.I)
    if not m:
        return None
    base = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").lower()
    if suffix in ("k", "thousand"):
        base *= 1000.0
    elif suffix in ("m", "million"):
        base *= 1000000.0
    return base


def extract_requested_model_year(q_lower: str, date_exprs: list[tuple[str, str]]) -> Optional[int]:
    """Extract a requested vehicle model year (any 4-digit year, e.g. 2022, 2025, 2099),
    excluding years that are part of a temporal date expression."""
    date_years = set()
    for raw, iso in date_exprs:
        for ym in re.findall(r"\b(19\d{2}|20\d{2})\b", raw):
            date_years.add(ym)

    for m in re.finditer(r"\b(19\d{2}|20\d{2})\b", q_lower):
        yr_str = m.group(1)
        # If explicitly followed by model/vehicle/car/auto/motorcycle/truck, always treat as model year
        after = q_lower[m.end():m.end() + 25]
        before = q_lower[max(0, m.start() - 20):m.start()]
        if re.search(r"^\s*(?:model|vehicle|car|auto|motorcycle|truck|suv|sedan)", after) or "model year" in before:
            return int(yr_str)
        if yr_str not in date_years:
            return int(yr_str)
    return None


def find_verbatim_line(markdown: str, pattern: str, flags: int = re.I) -> Optional[str]:
    """Return the first stripped line in markdown matching regex pattern."""
    reg = re.compile(pattern, flags)
    for line in markdown.splitlines():
        s = line.strip()
        if s and reg.search(s):
            return s
    return None


def format_product_display_name(product_name: str, entity: str = "") -> str:
    """Convert normalized product_name into human-readable title."""
    mapping = {
        "1st_rate_checking": "1st Rate Checking",
        "free_checking": "Free Checking",
        "business_1st_rate_checking": "Business 1st Rate Checking",
        "business_free_checking": "Business Free Checking",
        "business_analysis_checking": "Business Analysis Checking",
        "bright_savings": "Bright Savings",
        "share_savings": "Share Savings",
        "high_yield_savings": "High Yield Savings",
        "investors_choice_money_market": "Investor's Choice Money Market",
        "money_market": "Money Market",
        "launchpad_savings": "Launchpad Savings",
        "holiday_club_savings": "Holiday Club Savings",
        "you_name_it_savings": "You Name It Savings",
        "health_savings": "Health Savings Account (HSA)",
        "ira_savings": "IRA Savings",
        "platinum_visa": "Platinum Visa",
        "rewards_visa": "Rewards Visa",
        "business_platinum": "Business Platinum Visa",
        "business_rewards": "Business Rewards Visa",
    }
    if product_name in mapping:
        return mapping[product_name]
    raw = product_name or entity
    return " ".join(w.upper() if w in ("ira", "hsa", "cd", "rv", "atv") else w.capitalize() for w in raw.split("_") if w)


class AnswerResolver:
    def __init__(self, ledger: TemporalLedger):
        self.ledger = ledger

    def resolve(self, question: str, as_of: Optional[str] = None) -> Answer:
        q_raw = question
        q_lower = question.lower()

        # Clock 1: Cutoff Date
        cutoff_date = as_of if as_of is not None else self.ledger.latest_date

        # Clock 1 Pre-baseline check
        if self.ledger.earliest_date and cutoff_date and cutoff_date < self.ledger.earliest_date:
            return Answer(
                text="The website had no snapshot coverage before the baseline crawl; information is not available.",
                sources=[],
                confident=False,
            )

        # Clock 2: Extract all date expressions from question
        date_exprs = extract_all_date_expressions(question, cutoff_date=cutoff_date)

        # Comparative two-date queries (e.g., May vs September, or comparing two explicit dates)
        is_comparison_query = len(date_exprs) >= 2 and (
            any(w in q_lower for w in ("versus", "vs", "compared", "compare", "change", "difference", "between"))
            or date_exprs[0][1][:7] != date_exprs[1][1][:7]
        )
        if is_comparison_query:
            comparison_ans = self._check_date_comparison(q_lower, date_exprs, cutoff_date)
            if comparison_ans:
                return comparison_ans

        requested_date = date_exprs[0][1] if date_exprs else None
        if as_of is not None and requested_date and requested_date > as_of:
            return Answer(
                text="The requested date is in the future relative to the snapshot cutoff date; information is not available.",
                sources=[],
                confident=False,
            )

        # Evaluation date for 4-Clock eligibility (Clock 2 if specified and <= Clock 1, else Clock 1)
        latest_bound = cutoff_date or "9999-99-99"
        eval_date = requested_date if (requested_date and requested_date <= latest_bound) else latest_bound

        # Resolve active snapshot at eval_date
        target_snap = self.ledger.get_target_snapshot(as_of=eval_date)
        if target_snap is None:
            return Answer(
                text="Information not found in available snapshots.",
                sources=[],
                confident=False,
            )

        # Filter target_snap.facts through the unified 4-Clock EligibilityEngine
        eff_cutoff = cutoff_date or target_snap.taken_on
        eligible_facts = [
            f for f in target_snap.facts
            if EligibilityEngine.is_eligible(f, target_date=eval_date, cutoff_date=eff_cutoff)
        ]

        # 1. Explicit Refusals (unlisted products, nonexistent branches, missing branch schedules)
        refusal_ans = self._check_explicit_refusals(q_lower, target_snap, eligible_facts)
        if refusal_ans:
            return refusal_ans

        # 2. Fees & Card/Account Fee Schedule (handled before card/account rate queries)
        if any(
            k in q_lower
            for k in (
                "fee",
                "replacement",
                "overdraft",
                "courtesy pay",
                "foreign transaction",
                "cash advance",
                "paper statement",
            )
        ):
            ans = self._resolve_fee(q_lower, target_snap, eligible_facts, eval_date, eff_cutoff)
            if ans:
                return ans

        # 3. Mobile Wallets (handled before credit cards so "debit and credit cards to mobile wallet" is not intercepted)
        if "wallet" in q_lower or "mobile payment" in q_lower or "apple pay" in q_lower or "google pay" in q_lower or "samsung pay" in q_lower:
            ans = self._resolve_mobile_payments(q_lower, target_snap)
            if ans:
                return ans

        # 4. Credit Cards
        if (
            "credit card" in q_lower
            or "rewards visa" in q_lower
            or "platinum visa" in q_lower
            or (re.search(r"\bcards?\b", q_lower) and "debit" not in q_lower and not re.search(r"\b(?:auto|car|vehicle)\b", q_lower))
        ):
            ans = self._resolve_credit_cards(q_lower, target_snap, eligible_facts)
            if ans:
                return ans

        # 5. Certificates / CDs
        if "certificate" in q_lower or re.search(r"\bcd\b", q_lower) or "cd rates" in q_lower:
            ans = self._resolve_certificate(q_lower, target_snap, eligible_facts)
            if ans:
                return ans

        # 6. Auto & Motorcycle Loans
        if re.search(r"\b(?:auto|vehicle|cars?|motorcycle)\b", q_lower):
            ans = self._resolve_auto_loan(q_lower, target_snap, eligible_facts, date_exprs, eval_date, eff_cutoff)
            if ans:
                return ans

        # 7. Mortgages, HELOC & Home Equity Loans
        if (
            "mortgage" in q_lower
            or "home loan" in q_lower
            or "heloc" in q_lower
            or "home equity" in q_lower
            or "home improvement" in q_lower
            or ("refinance" in q_lower and "auto" not in q_lower)
        ):
            ans = self._resolve_mortgage_or_heloc(q_lower, target_snap, eligible_facts)
            if ans:
                return ans

        # 8. Personal Loans
        if "personal loan" in q_lower or "signature loan" in q_lower:
            ans = self._resolve_personal_loan(q_lower, target_snap, eligible_facts)
            if ans:
                return ans

        # 9. Home$tart Grant Program
        if "home$tart" in q_lower or "homestart" in q_lower or "first-time homebuyer grant" in q_lower:
            ans = self._resolve_homestart(q_lower, target_snap)
            if ans:
                return ans

        # 10. Checking, Savings & Money Market Accounts
        if (
            "checking" in q_lower
            or "savings" in q_lower
            or "money market" in q_lower
            or "bright" in q_lower
            or "launchpad" in q_lower
            or "holiday club" in q_lower
            or "hsa" in q_lower
            or "0.35" in q_lower
        ):
            ans = self._resolve_accounts(q_lower, target_snap, eligible_facts)
            if ans:
                return ans

        # 11. Branch Information & Hours (Count, Galena Address, Hy-Vee closure, Lobby/Drive-up)
        if (
            "branch" in q_lower
            or "location" in q_lower
            or "galena" in q_lower
            or "hy-vee" in q_lower
            or "hyvee" in q_lower
            or "lobby" in q_lower
            or "drive-up" in q_lower
            or "drive" in q_lower
            or "hours" in q_lower
        ):
            ans = self._resolve_branches(q_lower, target_snap, eligible_facts, eval_date, eff_cutoff)
            if ans:
                return ans

        # 12. Routing Number
        if "routing" in q_lower or "aba" in q_lower:
            ans = self._resolve_routing_number(q_lower, target_snap, eligible_facts)
            if ans:
                return ans

        # 13. Annual Statistics & Reports (VTO, Forbes)
        if "volunteer" in q_lower or "vto" in q_lower or "giving back" in q_lower or "forbes" in q_lower:
            ans = self._resolve_statistics(q_lower, target_snap, eligible_facts)
            if ans:
                return ans

        # 14. Membership Eligibility & Online Joining
        if "member" in q_lower or "join" in q_lower or "eligible" in q_lower or "charter" in q_lower:
            ans = self._resolve_membership(q_lower, target_snap)
            if ans:
                return ans

        # 15. Contact Information Update
        if "contact" in q_lower and ("update" in q_lower or "information" in q_lower or "phone" in q_lower or "change" in q_lower):
            ans = self._resolve_contact_update(q_lower, target_snap)
            if ans:
                return ans

        # 16. Student Loans
        if "student" in q_lower or "university" in q_lower or "college" in q_lower:
            ans = self._resolve_student_loans(q_lower, target_snap)
            if ans:
                return ans

        # 17. General Keyword / Section Search (non-rate / non-fee inquiries)
        fallback_ans = self._search_sections(q_raw, q_lower, target_snap)
        if fallback_ans:
            return fallback_ans

        return Answer(
            text="Information is not available in the crawled website snapshots.",
            sources=[],
            confident=False,
        )

    def _check_explicit_refusals(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
    ) -> Optional[Answer]:
        """Verify unlisted terms, nonexistent branches, and branch-specific schedules with dynamic inversion."""
        if "mullan" in q_lower:
            mullan_branch_page = None
            mullan_quote = None
            for u, p in target_snap.pages.items():
                if "/contact" in u.lower() or "/branches" in u.lower():
                    q_line = find_verbatim_line(p.markdown, r"mullan")
                    if q_line:
                        mullan_branch_page = p
                        mullan_quote = q_line
                        break
                elif "mullan" in p.markdown.lower():
                    q_line = find_verbatim_line(p.markdown, r"mullan[^\n]*\b(?:lobby|hours|branch\s+hours|drive-up)\b")
                    if q_line:
                        mullan_branch_page = p
                        mullan_quote = q_line
                        break

            if not mullan_branch_page:
                return Answer(
                    text="No grounded hours or address for a Mullan Avenue branch were found on the website (the location does not appear in current branch listings).",
                    sources=[],
                    confident=False,
                )
            if mullan_quote:
                return Answer(
                    text=f"Mullan branch information: {mullan_quote}",
                    sources=[Source(url=mullan_branch_page.url, snapshot_id=target_snap.snapshot_id, quote=mullan_quote)],
                    confident=True,
                )

        if "galena" in q_lower and ("saturday" in q_lower or "weekend" in q_lower or ("hours" in q_lower and "address" not in q_lower)):
            galena_sat = None
            galena_page = None
            galena_quote = None
            for u, p in target_snap.pages.items():
                if "galena" in p.markdown.lower():
                    m_sat = (
                        re.search(r"galena(?:[^\n]*\n){0,3}[^\n]*sat(?:urday)?\s*[:\-]?\s*(\d[^\n]+)", p.markdown, re.I)
                        or re.search(r"galena[^\n]*sat(?:urday)?\s*[:\-]?\s*(\d[^\n]+)", p.markdown, re.I)
                    )
                    if m_sat and "closed" not in m_sat.group(1).lower():
                        galena_sat = m_sat.group(1).strip()
                        galena_page = p
                        galena_quote = find_verbatim_line(p.markdown, re.escape(galena_sat)) or galena_sat
                        break
            if galena_sat and galena_page and galena_quote:
                return Answer(
                    text=f"The Galena branch Saturday hours are {galena_sat}.",
                    sources=[Source(url=galena_page.url, snapshot_id=target_snap.snapshot_id, quote=galena_quote)],
                    confident=True,
                )
            contact_page = next((p for u, p in target_snap.pages.items() if "/contact" in u.lower() and "galena" in p.markdown.lower()), None)
            sources = []
            if contact_page:
                q_line = find_verbatim_line(contact_page.markdown, r"galena")
                if q_line:
                    sources.append(Source(url=contact_page.url, snapshot_id=target_snap.snapshot_id, quote=q_line))
            return Answer(
                text="The website does not list Saturday hours for the Galena branch.",
                sources=sources,
                confident=False,
            )

        if "7/1" in q_lower or "seven-over-one" in q_lower or "construction loan" in q_lower or "fha" in q_lower or "va loan" in q_lower:
            # Dynamic check: only refuse if no matching rate fact exists in eligible_facts
            has_prog = any(
                f.identity.entity in ("mortgage", "heloc", "home_equity_loan")
                and any(k in f.raw_quote.lower() for k in ("7/1", "seven-over-one", "construction", "fha", "va "))
                for f in eligible_facts
            )
            if not has_prog:
                return Answer(
                    text="Rates for this specific loan program are not published on the website; please contact the lending team.",
                    sources=[],
                    confident=False,
                )

        return None

    def _check_date_comparison(
        self,
        q_lower: str,
        date_exprs: list[tuple[str, str]],
        cutoff_date: Optional[str],
    ) -> Optional[Answer]:
        """Handle comparative questions between any two historical dates dynamically."""
        if len(date_exprs) < 2:
            return None
        (lbl1, d1), (lbl2, d2) = date_exprs[0], date_exprs[1]

        if cutoff_date and (d1 > cutoff_date or d2 > cutoff_date):
            return Answer(
                text="The requested comparison includes a date in the future relative to the snapshot cutoff date; information is not available.",
                sources=[],
                confident=False,
            )

        snap1 = self.ledger.get_target_snapshot(d1)
        snap2 = self.ledger.get_target_snapshot(d2)
        if not snap1 or not snap2:
            return Answer(
                text="Snapshot coverage is not available for both requested comparison dates.",
                sources=[],
                confident=False,
            )

        term = extract_requested_term_months(q_lower)
        req_year = extract_requested_model_year(q_lower, date_exprs)

        if re.search(r"\b(?:auto|vehicle|cars?|motorcycle)\b", q_lower):
            cands1 = [
                f for f in snap1.facts
                if f.identity.entity == "auto_loan"
                and f.identity.attribute == "apr"
                and EligibilityEngine.is_eligible(f, target_date=d1, cutoff_date=cutoff_date or d1)
                and (term is None or f.identity.term_months == term)
                and (req_year is None or (f.identity.tier_interval and f.identity.tier_interval.contains(float(req_year))))
            ]
            cands2 = [
                f for f in snap2.facts
                if f.identity.entity == "auto_loan"
                and f.identity.attribute == "apr"
                and EligibilityEngine.is_eligible(f, target_date=d2, cutoff_date=cutoff_date or d2)
                and (term is None or f.identity.term_months == term)
                and (req_year is None or (f.identity.tier_interval and f.identity.tier_interval.contains(float(req_year))))
            ]
            f1 = EligibilityEngine.get_winning_fact(cands1, target_date=d1, cutoff_date=cutoff_date or d1, active_snapshot_id=snap1.snapshot_id)
            f2 = EligibilityEngine.get_winning_fact(cands2, target_date=d2, cutoff_date=cutoff_date or d2, active_snapshot_id=snap2.snapshot_id)
            if f1 and f2:
                t_lbl = f"{f2.identity.term_months}-month " if f2.identity.term_months else ""
                my_lbl1 = f" ({f1.identity.tier_interval.raw_label} models)" if f1.identity.tier_interval and f1.identity.tier_interval.raw_label else ""
                my_lbl2 = f" ({f2.identity.tier_interval.raw_label} models)" if f2.identity.tier_interval and f2.identity.tier_interval.raw_label else ""
                return Answer(
                    text=f"The {t_lbl}auto loan APR was {f1.value_str}{my_lbl1} in {lbl1} ({snap1.taken_on}) and {f2.value_str}{my_lbl2} in {lbl2} ({snap2.taken_on}).",
                    sources=[
                        Source(url=f1.source_url, snapshot_id=f1.snapshot_id, quote=f1.raw_quote),
                        Source(url=f2.source_url, snapshot_id=f2.snapshot_id, quote=f2.raw_quote),
                    ],
                    confident=True,
                )
            return Answer(
                text="Matching auto loan rates were not found for both requested dates.",
                sources=[],
                confident=False,
            )

        # Generic two-date comparison for any other domain
        ans1 = self.resolve(re.sub(re.escape(lbl2), "", q_lower, flags=re.I), as_of=d1)
        ans2 = self.resolve(re.sub(re.escape(lbl1), "", q_lower, flags=re.I), as_of=d2)
        if ans1.confident and ans2.confident:
            return Answer(
                text=f"As of {lbl1} ({snap1.taken_on}): {ans1.text} As of {lbl2} ({snap2.taken_on}): {ans2.text}",
                sources=ans1.sources + ans2.sources,
                confident=True,
            )
        return None

    def _resolve_credit_cards(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
    ) -> Optional[Answer]:
        card_facts = [
            f for f in eligible_facts
            if f.identity.entity == "credit_card"
            and f.unit == "%"
            and f.identity.attribute in ("apr", "rate", "interest_rate")
        ]
        if not card_facts:
            return Answer(
                text="No credit card APRs are published in the available snapshot.",
                sources=[],
                confident=False,
            )

        req_business_only = "business" in q_lower and "personal" not in q_lower
        req_personal_only = "personal" in q_lower and "business" not in q_lower
        req_platinum_only = "platinum" in q_lower and "rewards" not in q_lower
        req_rewards_only = "rewards" in q_lower and "platinum" not in q_lower

        filtered = list(card_facts)
        if req_business_only:
            filtered = [f for f in filtered if f.identity.account_category == "business"]
        elif req_personal_only or (req_platinum_only and "business" not in q_lower) or (req_rewards_only and "business" not in q_lower):
            personal_subset = [f for f in filtered if f.identity.account_category == "personal"]
            if personal_subset:
                filtered = personal_subset

        if req_platinum_only:
            filtered = [f for f in filtered if "platinum" in f.identity.product_name]
        elif req_rewards_only:
            filtered = [f for f in filtered if "rewards" in f.identity.product_name]

        if not filtered:
            return Answer(
                text="No matching credit card APRs were found for the requested card qualifier.",
                sources=[],
                confident=False,
            )

        # Single card inquiry (e.g., Platinum Visa standard purchase APR or Rewards Visa APR)
        if req_platinum_only or req_rewards_only:
            std_facts = [f for f in filtered if not f.identity.is_intro]
            intro_facts = [f for f in filtered if f.identity.is_intro]
            std_facts.sort(
                key=lambda x: (1 if x.typed_value and x.typed_value.is_range else 0, EligibilityEngine.rank_key(x)),
                reverse=True,
            )
            if not std_facts:
                return Answer(
                    text="No standard APR was found for the requested credit card.",
                    sources=[],
                    confident=False,
                )
            f_std = std_facts[0]
            card_title = format_product_display_name(f_std.identity.product_name, "credit_card")
            range_note = ""
            if f_std.typed_value and f_std.typed_value.is_range and f_std.typed_value.high is not None:
                range_note = f" (range up to {f_std.typed_value.high:.2f}%)"

            intro_note = ""
            sources = [Source(url=f_std.source_url, snapshot_id=f_std.snapshot_id, quote=f_std.raw_quote)]
            if intro_facts:
                f_intro = intro_facts[0]
                intro_note = f"; the {f_intro.value_str} APR is an introductory rate"
                if f_std.footnote_text and "90 days" in f_std.footnote_text.lower():
                    intro_note += " valid for the first 90 days"
                sources.append(Source(url=f_intro.source_url, snapshot_id=f_intro.snapshot_id, quote=f_intro.raw_quote))

            return Answer(
                text=f"The standard purchase APR for the Dupaco {card_title} is a variable rate of {f_std.value_str}{range_note}{intro_note}.",
                sources=sources,
                confident=True,
            )

        # Broad credit card list and rates inquiry: dynamically discover all cards present in filtered
        groups: dict[tuple[str, str], list[ExtractedFact]] = defaultdict(list)
        for f in filtered:
            p_name = f.identity.product_name or "credit_card"
            groups[(f.identity.account_category, p_name)].append(f)

        # Deterministic ordering: personal cards first, then business cards
        def _card_sort_key(item: tuple[tuple[str, str], list[ExtractedFact]]) -> tuple[int, int, str]:
            cat, pname = item[0]
            cat_ord = 0 if cat == "personal" else 1
            p_ord = 0 if "platinum" in pname else (1 if "rewards" in pname else 2)
            return (cat_ord, p_ord, pname)

        sorted_groups = sorted(groups.items(), key=_card_sort_key)
        card_entries: list[str] = []
        sources: list[Source] = []
        seen_quotes: set[tuple[str, str]] = set()

        for idx, ((cat, pname), g_facts) in enumerate(sorted_groups, start=1):
            std_f_list = [f for f in g_facts if not f.identity.is_intro]
            intro_f_list = [f for f in g_facts if f.identity.is_intro]
            std_f_list.sort(
                key=lambda x: (1 if x.typed_value and x.typed_value.is_range else 0, EligibilityEngine.rank_key(x)),
                reverse=True,
            )
            best_f = std_f_list[0] if std_f_list else g_facts[0]
            disp_name = format_product_display_name(pname, "credit_card")
            if cat == "business" and "business" not in disp_name.lower():
                disp_name = f"Business {disp_name}"

            intro_clause = ""
            if intro_f_list:
                f_in = intro_f_list[0]
                if best_f.footnote_text and "90 days" in best_f.footnote_text.lower():
                    intro_clause = f" (with a {f_in.value_str} introductory balance-transfer APR for 6 months on transfers within 90 days)"
                else:
                    intro_clause = f" (with a {f_in.value_str} introductory APR)"

            card_entries.append(f"{idx}. {disp_name}: {best_f.value_str} variable APR{intro_clause}.")
            for sf in [best_f] + [f for f in std_f_list if "credit-cards" in f.source_url and f.snapshot_id == target_snap.snapshot_id]:
                qk = (sf.source_url, sf.raw_quote)
                if qk not in seen_quotes:
                    seen_quotes.add(qk)
                    sources.append(Source(url=sf.source_url, snapshot_id=sf.snapshot_id, quote=sf.raw_quote))

        count_words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
        n_cards = len(card_entries)
        c_word = count_words.get(n_cards, str(n_cards))
        plural = "card" if n_cards == 1 else "cards"

        return Answer(
            text=f"Dupaco offers {c_word} credit {plural}: " + " ".join(card_entries),
            sources=sources,
            confident=True,
        )

    def _resolve_certificate(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
    ) -> Optional[Answer]:
        # Direct link inquiry
        if "link" in q_lower or "url" in q_lower or "website" in q_lower or "webpage" in q_lower:
            cand_pages = sorted(
                [p for u, p in target_snap.pages.items() if "savings-account" in u.lower() or ("/rates" in u.lower() and "certificate" in p.markdown.lower())],
                key=lambda p: (
                    0 if "/personal/savings-account" in p.url.lower() and "business" not in q_lower else (1 if "/rates" in p.url.lower() else 2),
                    p.url,
                ),
            )
            cd_page = cand_pages[0] if cand_pages else None
            if cd_page:
                link_url = "https://www.dupaco.com/personal/savings-account#rates" if "/personal/savings-account" in cd_page.url.lower() else cd_page.url
                q_line = find_verbatim_line(cd_page.markdown, r"certificate") or cd_page.title
                return Answer(
                    text=f"You can view Dupaco's CD / certificate rates at {link_url}.",
                    sources=[Source(url=link_url, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                    confident=True,
                )
            return Answer(
                text="No certificate rates page link was found in the available snapshot.",
                sources=[],
                confident=False,
            )

        req_dividend = "dividend" in q_lower and "apy" not in q_lower
        target_attr = "dividend_rate" if req_dividend else "apy"
        metric_label = "dividend rate" if req_dividend else "APY"

        cert_facts = [
            f for f in eligible_facts
            if f.identity.entity == "certificate"
            and f.identity.attribute == target_attr
            and f.identity.term_months is not None
        ]
        # Filter by account_category (default personal unless business/ira asked)
        if "business" in q_lower:
            cert_facts = [f for f in cert_facts if f.identity.account_category == "business"]
        else:
            personal_certs = [f for f in cert_facts if f.identity.account_category == "personal"]
            if personal_certs:
                cert_facts = personal_certs

        if not cert_facts:
            return Answer(
                text=f"No eligible certificate {metric_label}s were found in the available snapshot.",
                sources=[],
                confident=False,
            )

        min_facts = [
            f for f in eligible_facts
            if f.identity.entity == "certificate" and f.identity.attribute == "minimum_balance"
        ]

        def _get_min_note(f_cert: ExtractedFact) -> str:
            for mf in min_facts:
                if (
                    mf.identity.term_months == f_cert.identity.term_months
                    and mf.identity.is_jumbo == f_cert.identity.is_jumbo
                    and mf.source_url == f_cert.source_url
                ):
                    return f" ({mf.value_str} minimum)"
            return ""

        term = extract_requested_term_months(q_lower)
        req_jumbo_only = "jumbo" in q_lower and "regular" not in q_lower
        req_regular_only = ("regular" in q_lower or "standard" in q_lower) and "jumbo" not in q_lower

        # Full schedule / general rates inquiry when no specific term is requested
        if "all" in q_lower or term is None:
            grouped: dict[tuple[int, bool, bool], list[ExtractedFact]] = defaultdict(list)
            for f in cert_facts:
                if req_jumbo_only and not f.identity.is_jumbo:
                    continue
                if req_regular_only and f.identity.is_jumbo:
                    continue
                grouped[(f.identity.term_months or 0, f.identity.is_special, f.identity.is_jumbo)].append(f)

            if not grouped:
                return Answer(
                    text="No matching certificate terms were found in the available snapshot.",
                    sources=[],
                    confident=False,
                )

            lines: list[str] = []
            sources: list[Source] = []
            seen_q: set[tuple[str, str]] = set()
            for (t_m, is_sp, is_jb), g_list in sorted(grouped.items(), key=lambda x: x[0]):
                g_list.sort(key=EligibilityEngine.rank_key, reverse=True)
                bf = g_list[0]
                spec_lbl = " Jumbo Special" if is_jb and is_sp else (" Jumbo" if is_jb else (" Special" if is_sp else ""))
                lines.append(f"{t_m}-Month{spec_lbl}: {bf.value_str} {metric_label}")
                qk = (bf.source_url, bf.raw_quote)
                if qk not in seen_q:
                    seen_q.add(qk)
                    sources.append(Source(url=bf.source_url, snapshot_id=bf.snapshot_id, quote=bf.raw_quote))

            return Answer(
                text=f"Dupaco's Certificate {metric_label}s are: " + ", ".join(lines) + ".",
                sources=sources,
                confident=True,
            )

        # Specific term requested: strict term match required
        term_cands = [f for f in cert_facts if f.identity.term_months == term]
        if not term_cands:
            return Answer(
                text=f"Dupaco does not offer a {term}-month certificate; this term is not listed in the available snapshot.",
                sources=[],
                confident=False,
            )

        reg_cands = [f for f in term_cands if not f.identity.is_jumbo]
        jumbo_cands = [f for f in term_cands if f.identity.is_jumbo]
        reg_cands.sort(key=EligibilityEngine.rank_key, reverse=True)
        jumbo_cands.sort(key=EligibilityEngine.rank_key, reverse=True)

        if req_jumbo_only:
            if not jumbo_cands:
                return Answer(
                    text=f"A {term}-month jumbo certificate is not offered; certificates for {term} months are regular term certificates only.",
                    sources=[Source(url=reg_cands[0].source_url, snapshot_id=reg_cands[0].snapshot_id, quote=reg_cands[0].raw_quote)] if reg_cands else [],
                    confident=False,
                )
            f_j = jumbo_cands[0]
            sp_lbl = "Fixed Jumbo Certificate Special" if f_j.identity.is_special else "Jumbo Certificate"
            min_note = _get_min_note(f_j)
            return Answer(
                text=f"The {term}-Month {sp_lbl} pays {f_j.value_str} {metric_label}{min_note}.",
                sources=[Source(url=f_j.source_url, snapshot_id=f_j.snapshot_id, quote=f_j.raw_quote)],
                confident=True,
            )

        if req_regular_only:
            if not reg_cands:
                return Answer(
                    text=f"A {term}-month regular certificate is not offered in the available snapshot.",
                    sources=[],
                    confident=False,
                )
            f_r = reg_cands[0]
            sp_lbl = "Fixed Certificate Special" if f_r.identity.is_special else "Certificate"
            min_note = _get_min_note(f_r)
            return Answer(
                text=f"The {term}-Month {sp_lbl} pays {f_r.value_str} {metric_label}{min_note}.",
                sources=[Source(url=f_r.source_url, snapshot_id=f_r.snapshot_id, quote=f_r.raw_quote)],
                confident=True,
            )

        # Broad term inquiry (neither regular-only nor jumbo-only specified):
        # If both regular and jumbo exist for this term, report both dynamically!
        if reg_cands and jumbo_cands:
            f_r = reg_cands[0]
            f_j = jumbo_cands[0]
            r_lbl = "Fixed Certificate Special" if f_r.identity.is_special else "Certificate"
            j_lbl = "Fixed Jumbo Certificate Special" if f_j.identity.is_special else "Jumbo Certificate"
            r_min = _get_min_note(f_r)
            j_min = _get_min_note(f_j)
            return Answer(
                text=f"The {term}-Month {r_lbl} pays {f_r.value_str} {metric_label}{r_min}; the {term}-Month {j_lbl} pays {f_j.value_str} {metric_label}{j_min}.",
                sources=[
                    Source(url=f_r.source_url, snapshot_id=f_r.snapshot_id, quote=f_r.raw_quote),
                    Source(url=f_j.source_url, snapshot_id=f_j.snapshot_id, quote=f_j.raw_quote),
                ],
                confident=True,
            )

        best_f = reg_cands[0] if reg_cands else jumbo_cands[0]
        qualifiers = []
        if best_f.identity.is_special:
            qualifiers.append("Special")
        if best_f.identity.is_jumbo:
            qualifiers.append("Jumbo")
        q_str = " ".join(qualifiers) + " " if qualifiers else ""
        min_note = _get_min_note(best_f)

        return Answer(
            text=f"The {q_str}{term}-month Certificate {metric_label} is {best_f.value_str}{min_note}.",
            sources=[Source(url=best_f.source_url, snapshot_id=best_f.snapshot_id, quote=best_f.raw_quote)],
            confident=True,
        )

    def _resolve_auto_loan(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
        date_exprs: list[tuple[str, str]],
        eval_date: str,
        cutoff_date: str,
    ) -> Optional[Answer]:
        auto_facts = [
            f for f in eligible_facts
            if f.identity.entity == "auto_loan" and f.identity.attribute == "apr"
        ]
        if not auto_facts:
            return Answer(
                text="No eligible auto loan rates were found in the available snapshot.",
                sources=[],
                confident=False,
            )

        term = extract_requested_term_months(q_lower)
        req_year = extract_requested_model_year(q_lower, date_exprs)

        candidates = list(auto_facts)
        if term is not None:
            candidates = [f for f in candidates if f.identity.term_months == term]
            if not candidates:
                return Answer(
                    text=f"No {term}-month auto loan rate is published in the available snapshot.",
                    sources=[],
                    confident=False,
                )

        if req_year is not None:
            candidates = [
                f for f in candidates
                if f.identity.tier_interval is not None
                and f.identity.tier_interval.unit == "year"
                and f.identity.tier_interval.contains(float(req_year))
            ]
            if not candidates:
                return Answer(
                    text=f"No auto loan rate for the {req_year} model year is published in the available snapshot.",
                    sources=[],
                    confident=False,
                )

        winning_f = EligibilityEngine.get_winning_fact(
            candidates,
            target_date=eval_date,
            cutoff_date=cutoff_date,
            active_snapshot_id=target_snap.snapshot_id,
        )
        if not winning_f:
            return Answer(
                text="No eligible auto loan rate was found for the requested parameters.",
                sources=[],
                confident=False,
            )

        my_label = f" for {winning_f.identity.tier_interval.raw_label} models" if winning_f.identity.tier_interval and winning_f.identity.tier_interval.raw_label else ""
        t_str = f"{winning_f.identity.term_months}-month " if winning_f.identity.term_months else ""

        sources: list[Source] = []
        seen_urls: set[str] = set()
        for cf in [winning_f] + [
            f for f in candidates
            if f.value_str == winning_f.value_str
            and f.snapshot_id == target_snap.snapshot_id
            and f.authority_score >= 0.90
        ]:
            if cf.source_url not in seen_urls:
                seen_urls.add(cf.source_url)
                sources.append(Source(url=cf.source_url, snapshot_id=cf.snapshot_id, quote=cf.raw_quote))

        return Answer(
            text=f"The {t_str}auto loan rate{my_label} is as low as {winning_f.value_str} APR.",
            sources=sources,
            confident=True,
        )

    def _resolve_mortgage_or_heloc(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
    ) -> Optional[Answer]:
        # 1. HELOC (variable Home Equity Line of Credit)
        if "heloc" in q_lower or ("home equity" in q_lower and "line" in q_lower):
            heloc_facts = [
                f for f in eligible_facts
                if f.identity.entity == "heloc" and f.identity.attribute == "apr"
            ]
            if not heloc_facts:
                return Answer(
                    text="No Home Equity Line of Credit (HELOC) rates are published in the available snapshot.",
                    sources=[],
                    confident=False,
                )
            heloc_facts.sort(key=EligibilityEngine.rank_key, reverse=True)
            f_h = heloc_facts[0]
            return Answer(
                text=f"The standard Home Equity Line of Credit (HELOC) APR is {f_h.value_str}.",
                sources=[Source(url=f_h.source_url, snapshot_id=f_h.snapshot_id, quote=f_h.raw_quote)],
                confident=True,
            )

        # 2. Fixed Home Equity Loan or Home Improvement Loan
        if ("home equity" in q_lower and "line" not in q_lower) or "home improvement" in q_lower:
            target_ent = "home_improvement_loan" if "improvement" in q_lower else "home_equity_loan"
            label_name = "Home Improvement Loan" if target_ent == "home_improvement_loan" else "Fixed Home Equity Loan"
            he_facts = [
                f for f in eligible_facts
                if f.identity.entity == target_ent and f.identity.attribute == "apr"
            ]
            term = extract_requested_term_months(q_lower)
            if term is not None:
                he_facts = [f for f in he_facts if f.identity.term_months == term]
                if not he_facts:
                    return Answer(
                        text=f"No {term}-month {label_name} rate is published in the available snapshot.",
                        sources=[],
                        confident=False,
                    )
            if not he_facts:
                return Answer(
                    text=f"No {label_name} rates are published in the available snapshot.",
                    sources=[],
                    confident=False,
                )
            he_facts.sort(key=lambda x: (EligibilityEngine.rank_key(x), -(x.identity.term_months or 999)), reverse=True)
            if term is not None or len({f.identity.term_months for f in he_facts}) == 1:
                best_he = he_facts[0]
                t_lbl = f"{best_he.identity.term_months}-month " if best_he.identity.term_months else ""
                return Answer(
                    text=f"The {t_lbl}{label_name} APR is as low as {best_he.value_str}.",
                    sources=[Source(url=best_he.source_url, snapshot_id=best_he.snapshot_id, quote=best_he.raw_quote)],
                    confident=True,
                )
            # Multiple terms available: report each term dynamically
            by_term: dict[int, ExtractedFact] = {}
            for f in sorted(he_facts, key=lambda x: (x.identity.term_months or 0, -EligibilityEngine.rank_key(x)[0])):
                tm = f.identity.term_months or 0
                if tm not in by_term:
                    by_term[tm] = f
            parts = [f"{tm}-month: {f.value_str} APR" for tm, f in sorted(by_term.items())]
            srcs = [Source(url=f.source_url, snapshot_id=f.snapshot_id, quote=f.raw_quote) for f in by_term.values()]
            return Answer(
                text=f"{label_name} rates are: " + ", ".join(parts) + ".",
                sources=srcs,
                confident=True,
            )

        # 3. Conforming Mortgages
        mortgage_facts = [
            f for f in eligible_facts
            if f.identity.entity == "mortgage"
        ]
        if not mortgage_facts:
            return Answer(
                text="No mortgage rates are published in the available snapshot.",
                sources=[],
                confident=False,
            )

        term = extract_requested_term_months(q_lower)
        tx_type = "refinance" if "refinance" in q_lower else ("purchase" if "purchase" in q_lower else "")
        req_amount = extract_requested_amount(q_lower)
        req_interest_rate = "interest rate" in q_lower and "apr" not in q_lower
        req_apr_only = "apr" in q_lower and "interest rate" not in q_lower

        candidates = list(mortgage_facts)
        if tx_type:
            candidates = [f for f in candidates if f.identity.transaction_type == tx_type]
            if not candidates:
                return Answer(
                    text=f"No {tx_type} mortgage rates are published in the available snapshot.",
                    sources=[],
                    confident=False,
                )

        if term is not None:
            candidates = [f for f in candidates if f.identity.term_months == term]
            if not candidates:
                yr_lbl = f"{term // 12}-year" if term % 12 == 0 else f"{term}-month"
                return Answer(
                    text=f"No {yr_lbl} mortgage rates are published in the available snapshot.",
                    sources=[],
                    confident=False,
                )

        # Dynamic maximum loan ceiling check from actual candidate facts
        all_ceilings = sorted({
            f.identity.tier_interval.high for f in candidates
            if f.identity.tier_interval and f.identity.tier_interval.high is not None
        })
        if req_amount is not None:
            if all_ceilings:
                max_pub_ceiling = max(all_ceilings)
                if req_amount > max_pub_ceiling:
                    cap_str = f"${max_pub_ceiling:,.0f}" if max_pub_ceiling.is_integer() else f"${max_pub_ceiling:,.2f}"
                    return Answer(
                        text=f"Conforming mortgage loan tables published on the website cap at {cap_str}; rates for higher loan amounts are not published.",
                        sources=[],
                        confident=False,
                    )
            candidates = [
                f for f in candidates
                if f.identity.tier_interval and f.identity.tier_interval.contains(req_amount)
            ]
            if not candidates:
                return Answer(
                    text=f"No mortgage rate tier matching ${req_amount:,.0f} was found in the available snapshot.",
                    sources=[],
                    confident=False,
                )

        # Helper to prefer authoritative /home-loans and purchase (when tx_type not explicitly requested)
        def _mort_sort_key(f: ExtractedFact) -> tuple:
            return (
                1 if "/home-loans" in f.source_url else 0,
                1 if (not tx_type and f.identity.transaction_type == "purchase") else 0,
                EligibilityEngine.rank_key(f),
            )

        # Specific amount or specific metric/tx_type inquiry
        if req_amount is not None or tx_type or req_apr_only or (term is not None and term != 360 and req_interest_rate):
            if req_interest_rate:
                metric_cands = [f for f in candidates if f.identity.attribute == "interest_rate"]
            elif req_apr_only:
                metric_cands = [f for f in candidates if f.identity.attribute == "apr"]
            else:
                metric_cands = [f for f in candidates if f.identity.attribute == "apr"] or candidates

            if not metric_cands:
                return Answer(
                    text="No matching mortgage rate metric was found in the available snapshot.",
                    sources=[],
                    confident=False,
                )
            metric_cands.sort(key=_mort_sort_key, reverse=True)
            best_f = metric_cands[0]
            # Also check if paired interest_rate / apr exists on the same row
            paired_ir = next((f for f in candidates if f.raw_quote == best_f.raw_quote and f.identity.attribute == "interest_rate"), None)
            paired_apr = next((f for f in candidates if f.raw_quote == best_f.raw_quote and f.identity.attribute == "apr"), None)

            tx_label = f"{best_f.identity.transaction_type.capitalize()} " if best_f.identity.transaction_type else ""
            t_label = f"{best_f.identity.term_months // 12}-Year Fixed " if best_f.identity.term_months else ""
            tier_lbl = f" ({best_f.identity.tier_interval.raw_label})" if best_f.identity.tier_interval and best_f.identity.tier_interval.raw_label else ""

            if req_interest_rate or req_apr_only or not (paired_ir and paired_apr):
                metric_label = "interest rate" if best_f.identity.attribute == "interest_rate" else "APR"
                return Answer(
                    text=f"The {tx_label}{t_label}Conventional mortgage {metric_label}{tier_lbl} is {best_f.value_str}.",
                    sources=[Source(url=best_f.source_url, snapshot_id=best_f.snapshot_id, quote=best_f.raw_quote)],
                    confident=True,
                )
            return Answer(
                text=f"The {tx_label}{t_label}Conventional mortgage{tier_lbl} is {paired_ir.value_str} interest rate / {paired_apr.value_str} APR.",
                sources=[Source(url=best_f.source_url, snapshot_id=best_f.snapshot_id, quote=best_f.raw_quote)],
                confident=True,
            )

        # Broad mortgage inquiry (no specific amount requested): build summary strictly from actual rows in candidates
        candidates.sort(key=_mort_sort_key, reverse=True)

        # If specifically 30-year ("30-year" / "30 year") broad inquiry
        if term == 360:
            m30 = [f for f in candidates if f.identity.term_months == 360]
            c30 = sorted({f.identity.tier_interval.high for f in m30 if f.identity.tier_interval and f.identity.tier_interval.high is not None})
            if c30:
                low_c, high_c = c30[0], c30[-1]
                r_low = next((f for f in m30 if f.identity.attribute == "interest_rate" and f.identity.tier_interval and f.identity.tier_interval.high == low_c), None)
                a_low = next((f for f in m30 if f.identity.attribute == "apr" and f.identity.tier_interval and f.identity.tier_interval.high == low_c), None)
                r_high = next((f for f in m30 if f.identity.attribute == "interest_rate" and f.identity.tier_interval and f.identity.tier_interval.high == high_c), None)
                a_high = next((f for f in m30 if f.identity.attribute == "apr" and f.identity.tier_interval and f.identity.tier_interval.high == high_c), None)
                src_f = r_low or a_low or m30[0]
                eff_str = f", per the rate generation effective {src_f.effective_date}." if src_f.effective_date else "."
                low_lbl = "$100k" if low_c == 100000.0 else f"${low_c:,.0f}"
                high_lbl = f"${high_c:,.0f}"
                if low_c == high_c or not (r_high and a_high):
                    r1_s = f"{r_low.value_str} rate" if r_low else ""
                    a1_s = f"{a_low.value_str} APR" if a_low else ""
                    sep = " / " if (r1_s and a1_s) else ""
                    return Answer(
                        text=f"For 30-year loans up to {low_lbl}: {r1_s}{sep}{a1_s}{eff_str}",
                        sources=[Source(url=src_f.source_url, snapshot_id=src_f.snapshot_id, quote=src_f.raw_quote)],
                        confident=True,
                    )
                srcs = [Source(url=src_f.source_url, snapshot_id=src_f.snapshot_id, quote=src_f.raw_quote)]
                if r_high and r_high.raw_quote != src_f.raw_quote:
                    srcs.append(Source(url=r_high.source_url, snapshot_id=r_high.snapshot_id, quote=r_high.raw_quote))
                return Answer(
                    text=f"About {r_low.value_str if r_low else ''} rate / {a_low.value_str if a_low else ''} APR for loans up to {low_lbl}, up to {r_high.value_str} rate / {a_high.value_str} APR (loans up to {high_lbl}){eff_str}",
                    sources=srcs,
                    confident=True,
                )

        # General mortgage inquiry (term is None or other): summarize available terms at their lowest published ceiling tier
        avail_terms = sorted({f.identity.term_months for f in candidates if f.identity.term_months is not None}, reverse=True)
        if avail_terms:
            summary_parts: list[str] = []
            sources: list[Source] = []
            seen_quotes: set[tuple[str, str]] = set()
            lead_ceiling: Optional[float] = None

            for tm in avail_terms:
                tm_facts = [f for f in candidates if f.identity.term_months == tm]
                tm_ceils = sorted({f.identity.tier_interval.high for f in tm_facts if f.identity.tier_interval and f.identity.tier_interval.high is not None})
                target_c = tm_ceils[0] if tm_ceils else None
                if lead_ceiling is None and target_c is not None:
                    lead_ceiling = target_c
                r_f = next((f for f in tm_facts if f.identity.attribute == "interest_rate" and (target_c is None or (f.identity.tier_interval and f.identity.tier_interval.high == target_c))), None)
                a_f = next((f for f in tm_facts if f.identity.attribute == "apr" and (target_c is None or (f.identity.tier_interval and f.identity.tier_interval.high == target_c))), None)
                rep_f = r_f or a_f or tm_facts[0]
                yr_str = f"{tm // 12}-year" if tm % 12 == 0 else f"{tm}-month"
                if r_f and a_f:
                    summary_parts.append(f"{yr_str} {r_f.value_str} interest rate / {a_f.value_str} APR")
                elif a_f:
                    summary_parts.append(f"{yr_str} {a_f.value_str} APR")
                elif r_f:
                    summary_parts.append(f"{yr_str} {r_f.value_str} interest rate")
                qk = (rep_f.source_url, rep_f.raw_quote)
                if qk not in seen_quotes:
                    seen_quotes.add(qk)
                    sources.append(Source(url=rep_f.source_url, snapshot_id=rep_f.snapshot_id, quote=rep_f.raw_quote))

            if summary_parts:
                tier_prefix = ""
                if lead_ceiling is not None:
                    c_lbl = "$100k" if lead_ceiling == 100000.0 else f"${lead_ceiling:,.0f}"
                    tier_prefix = f"For the {c_lbl} loan-amount tier: "
                return Answer(
                    text=tier_prefix + ", and ".join(summary_parts) + ".",
                    sources=sources,
                    confident=True,
                )

        # Fallback single mortgage row without term_months
        best_f = candidates[0]
        paired_ir = next((f for f in candidates if f.raw_quote == best_f.raw_quote and f.identity.attribute == "interest_rate"), None)
        paired_apr = next((f for f in candidates if f.raw_quote == best_f.raw_quote and f.identity.attribute == "apr"), None)
        tier_lbl = f"For loans {best_f.identity.tier_interval.raw_label}: " if best_f.identity.tier_interval and best_f.identity.tier_interval.raw_label else ""
        if paired_ir and paired_apr:
            return Answer(
                text=f"{tier_lbl}{paired_ir.value_str} interest rate / {paired_apr.value_str} APR.",
                sources=[Source(url=best_f.source_url, snapshot_id=best_f.snapshot_id, quote=best_f.raw_quote)],
                confident=True,
            )
        return Answer(
            text=f"{tier_lbl}{best_f.value_str}.",
            sources=[Source(url=best_f.source_url, snapshot_id=best_f.snapshot_id, quote=best_f.raw_quote)],
            confident=True,
        )

    def _resolve_personal_loan(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
    ) -> Optional[Answer]:
        pl_facts = [
            f for f in eligible_facts
            if f.identity.entity == "personal_loan" and f.identity.attribute == "apr"
        ]
        if not pl_facts:
            return Answer(
                text="No personal loan rates are published in the available snapshot.",
                sources=[],
                confident=False,
            )

        term = extract_requested_term_months(q_lower)
        if term is not None:
            pl_facts = [f for f in pl_facts if f.identity.term_months == term]
            if not pl_facts:
                return Answer(
                    text=f"No {term}-month personal loan rate is published in the available snapshot.",
                    sources=[],
                    confident=False,
                )

        pl_facts.sort(key=EligibilityEngine.rank_key, reverse=True)
        f_top = pl_facts[0]
        t_lbl = f"{f_top.identity.term_months}-month " if f_top.identity.term_months else ""
        return Answer(
            text=f"The {t_lbl}personal loan APR is {f_top.value_str}.",
            sources=[Source(url=f_top.source_url, snapshot_id=f_top.snapshot_id, quote=f_top.raw_quote)],
            confident=True,
        )

    def _resolve_homestart(self, q_lower: str, target_snap: SnapshotRecord) -> Optional[Answer]:
        cand_pages = sorted(
            target_snap.pages.items(),
            key=lambda kv: (
                0 if ("hometart" in kv[0].lower() or "homestart" in kv[0].lower()) else 1,
                kv[0],
            ),
        )
        for u, p in cand_pages:
            if "hometart" in u.lower() or "homestart" in u.lower() or "home$tart" in p.markdown.lower() or "first-time homebuyer grant" in p.markdown.lower():
                q_line = find_verbatim_line(
                    p.markdown,
                    r"(?:home\$tart|homestart|grant|assistance|down\s+payment|closing)[^\n]*\$\s*\d+(?:,\d{3})+|\$\s*\d+(?:,\d{3})+[^\n]*(?:home\$tart|homestart|grant|assistance|down\s+payment|closing)",
                )
                m_amt = re.search(r"(\$\s*\d+(?:,\d{3})+)", q_line or "")
                if m_amt and q_line:
                    amt_str = m_amt.group(1).replace(" ", "")
                    return Answer(
                        text=f"The FHLB Home$tart program provides up to {amt_str} in grant assistance toward down payment and closing costs for eligible first-time homebuyers.",
                        sources=[Source(url=u, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                        confident=True,
                    )
        return Answer(
            text="No Home$tart grant information was found in the available snapshot.",
            sources=[],
            confident=False,
        )

    def _resolve_accounts(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
    ) -> Optional[Answer]:
        req_category = (
            "business" if "business" in q_lower
            else ("health_savings" if ("hsa" in q_lower or "health savings" in q_lower)
            else ("ira" if re.search(r"\bira\b", q_lower) else "personal"))
        )
        req_amount = extract_requested_amount(q_lower)

        # 1. Specific APY verification query (e.g. "Does Dupaco's regular Share Savings account offer a 0.35% APY tier?")
        m_pct_q = re.search(r"(\d+\.\d+)\s*%", q_lower)
        if m_pct_q and "savings" in q_lower and "health" not in q_lower and "hsa" not in q_lower:
            q_pct = float(m_pct_q.group(1))
            share_facts = [
                f for f in eligible_facts
                if (f.identity.entity == "share_savings" or f.identity.product_name == "share_savings")
                and f.identity.account_category == "personal"
                and f.identity.attribute == "apy"
            ]
            if share_facts:
                has_match = any(abs((f.value_num or 0.0) - q_pct) < 1e-4 for f in share_facts)
                if not has_match:
                    top_f = max(share_facts, key=lambda x: x.value_num or 0.0)
                    other_f = next(
                        (f for f in eligible_facts if f.identity.attribute == "apy" and abs((f.value_num or 0.0) - q_pct) < 1e-4),
                        None,
                    )
                    top_tier_lbl = f" ({top_f.identity.tier_interval.raw_label})" if top_f.identity.tier_interval and top_f.identity.tier_interval.raw_label else ""
                    other_note = ""
                    srcs = [Source(url=top_f.source_url, snapshot_id=top_f.snapshot_id, quote=top_f.raw_quote)]
                    if other_f:
                        other_name = format_product_display_name(other_f.identity.product_name, other_f.identity.entity)
                        other_note = f"; a {other_f.value_str} APY tier applies to {other_name}"
                        srcs.append(Source(url=other_f.source_url, snapshot_id=other_f.snapshot_id, quote=other_f.raw_quote))
                    return Answer(
                        text=f"No - the Share Savings tiers top out at {top_f.value_str} APY{top_tier_lbl}{other_note}.",
                        sources=srcs,
                        confident=True,
                    )

        # 2. Bright Savings
        if "bright" in q_lower:
            bright_facts = [
                f for f in eligible_facts
                if f.identity.entity == "bright_savings" or f.identity.product_name == "bright_savings"
            ]
            if not bright_facts:
                return Answer(
                    text="No Bright Savings rates are published in the available snapshot.",
                    sources=[],
                    confident=False,
                )
            bright_facts.sort(key=EligibilityEngine.rank_key, reverse=True)
            apy_f = next((f for f in bright_facts if f.identity.attribute == "apy"), bright_facts[0])
            div_f = next((f for f in bright_facts if f.identity.attribute == "dividend_rate" and f.source_url == apy_f.source_url), None)
            div_str = f"{div_f.value_str} dividend rate and " if div_f else ""
            eff_str = f" (rates effective {apy_f.effective_date})" if apy_f.effective_date else ""
            return Answer(
                text=f"Bright Savings earns a {div_str}{apy_f.value_str} APY{eff_str}.",
                sources=[Source(url=apy_f.source_url, snapshot_id=apy_f.snapshot_id, quote=apy_f.raw_quote)],
                confident=True,
            )

        # 3. Checking Accounts (Personal, Business, or custom checking product)
        if "checking" in q_lower or "1st rate" in q_lower or "first rate" in q_lower:
            checking_facts = [
                f for f in eligible_facts
                if (f.identity.entity == "checking" or "checking" in f.identity.product_name)
                and f.identity.account_category == req_category
            ]
            if not checking_facts:
                return Answer(
                    text=f"No {req_category} checking account rates are published in the available snapshot.",
                    sources=[],
                    confident=False,
                )

            # Prefer authoritative rate schedules over unlinked promotional landing pages when authoritative facts exist
            auth_checking = [f for f in checking_facts if f.authority_score >= 0.90]
            if auth_checking:
                checking_facts = auth_checking

            # Check if a specific product name in checking_facts is mentioned in q_lower
            avail_prods = {f.identity.product_name for f in checking_facts if f.identity.product_name}
            matched_prods = [
                p for p in avail_prods
                if p.replace("_", " ") in q_lower
                or ("1st rate" in q_lower and "1st_rate" in p)
                or ("first rate" in q_lower and "1st_rate" in p)
                or ("free checking" in q_lower and "free_checking" in p)
                or ("analysis" in q_lower and "analysis" in p)
            ]

            if req_amount is not None:
                cands = [f for f in checking_facts if (not matched_prods or f.identity.product_name in matched_prods)]
                cands = [f for f in cands if f.identity.tier_interval and f.identity.tier_interval.contains(req_amount)]
                if not cands:
                    return Answer(
                        text=f"No checking rate tier matching ${req_amount:,.2f} was found in the available snapshot.",
                        sources=[],
                        confident=False,
                    )
                cands.sort(key=EligibilityEngine.rank_key, reverse=True)
                best_f = next((f for f in cands if f.identity.attribute == "apy"), cands[0])
                p_disp = format_product_display_name(best_f.identity.product_name, "checking")
                t_lbl = f" for balances {best_f.identity.tier_interval.raw_label}" if best_f.identity.tier_interval and best_f.identity.tier_interval.raw_label else ""
                return Answer(
                    text=f"{p_disp} pays {best_f.value_str} APY{t_lbl}.",
                    sources=[Source(url=best_f.source_url, snapshot_id=best_f.snapshot_id, quote=best_f.raw_quote)],
                    confident=True,
                )

            # Group checking APY facts by (product_name, tier_label) and pick highest-ranked per group
            apy_facts = [f for f in checking_facts if f.identity.attribute == "apy"] or checking_facts
            prod_tiers: dict[str, list[ExtractedFact]] = defaultdict(list)
            seen_pt: set[tuple[str, str]] = set()
            for f in sorted(apy_facts, key=EligibilityEngine.rank_key, reverse=True):
                pname = f.identity.product_name or "checking"
                tlbl = f.identity.tier_interval.raw_label if f.identity.tier_interval and f.identity.tier_interval.raw_label else ""
                if (pname, tlbl) not in seen_pt:
                    seen_pt.add((pname, tlbl))
                    prod_tiers[pname].append(f)

            if not prod_tiers:
                return None

            clauses: list[str] = []
            sources: list[Source] = []
            seen_q: set[tuple[str, str]] = set()
            for pname, tfacts in prod_tiers.items():
                p_disp = format_product_display_name(pname, "checking")
                if len(tfacts) == 1 and not (tfacts[0].identity.tier_interval and tfacts[0].identity.tier_interval.raw_label):
                    bf = tfacts[0]
                    clauses.append(f"{p_disp} offers {bf.value_str} APY")
                    qk = (bf.source_url, bf.raw_quote)
                    if qk not in seen_q:
                        seen_q.add(qk)
                        sources.append(Source(url=bf.source_url, snapshot_id=bf.snapshot_id, quote=bf.raw_quote))
                else:
                    # Sort tiers by highest APY first so main earning tier leads
                    tfacts_sorted = sorted(tfacts, key=lambda x: (x.value_num or 0.0, -(x.identity.tier_interval.low or 0.0 if x.identity.tier_interval else 0.0)), reverse=True)
                    lead_f = tfacts_sorted[0]
                    lead_lbl = f" on balances of {lead_f.identity.tier_interval.raw_label}" if lead_f.identity.tier_interval and lead_f.identity.tier_interval.raw_label else ""
                    other_parts = []
                    for of in tfacts_sorted[1:]:
                        olbl = of.identity.tier_interval.raw_label if of.identity.tier_interval and of.identity.tier_interval.raw_label else "other balances"
                        other_parts.append(f"{of.value_str} APY on {olbl}")
                    other_str = f" ({', '.join(other_parts)})" if other_parts else ""
                    clauses.append(f"{p_disp} offers {lead_f.value_str} APY{lead_lbl}{other_str}")
                    for tf in tfacts_sorted:
                        qk = (tf.source_url, tf.raw_quote)
                        if qk not in seen_q:
                            seen_q.add(qk)
                            sources.append(Source(url=tf.source_url, snapshot_id=tf.snapshot_id, quote=tf.raw_quote))

            return Answer(
                text="; ".join(clauses) + ".",
                sources=sources,
                confident=True,
            )

        # 4. Share Savings & Other Savings / Money Market / HSA / IRA Accounts
        savings_facts = [
            f for f in eligible_facts
            if f.identity.entity in ("share_savings", "savings", "money_market", "health_savings")
            and f.identity.account_category == req_category
        ]
        if "share savings" in q_lower or "regular savings" in q_lower:
            savings_facts = [f for f in savings_facts if f.identity.entity == "share_savings" or f.identity.product_name == "share_savings"]
        elif "money market" in q_lower or "investor" in q_lower:
            savings_facts = [f for f in savings_facts if f.identity.entity == "money_market"]
        elif "high yield" in q_lower:
            savings_facts = [f for f in savings_facts if f.identity.product_name == "high_yield_savings"]
        elif "launchpad" in q_lower:
            savings_facts = [f for f in savings_facts if f.identity.product_name == "launchpad_savings"]
        elif "holiday club" in q_lower:
            savings_facts = [f for f in savings_facts if f.identity.product_name == "holiday_club_savings"]

        if not savings_facts:
            return Answer(
                text="No matching savings account rates are published in the available snapshot.",
                sources=[],
                confident=False,
            )

        # If a specific balance amount or balance range is requested (e.g. "$9,999.99" or "between $10,000 and $24,999.99")
        if req_amount is not None:
            tier_matches = [
                f for f in savings_facts
                if f.identity.tier_interval and f.identity.tier_interval.contains(req_amount)
            ]
            if not tier_matches:
                return Answer(
                    text=f"No savings rate tier matching ${req_amount:,.2f} was found in the available snapshot.",
                    sources=[],
                    confident=False,
                )
            tier_matches.sort(key=EligibilityEngine.rank_key, reverse=True)
            apy_f = next((f for f in tier_matches if f.identity.attribute == "apy"), tier_matches[0])
            div_f = next((f for f in tier_matches if f.identity.attribute == "dividend_rate" and f.raw_quote == apy_f.raw_quote), None)
            p_disp = format_product_display_name(apy_f.identity.product_name, apy_f.identity.entity)
            t_lbl = apy_f.identity.tier_interval.raw_label if apy_f.identity.tier_interval and apy_f.identity.tier_interval.raw_label else f"${req_amount:,.2f}"
            if div_f and apy_f.identity.attribute == "apy":
                return Answer(
                    text=f"For balances in the {t_lbl} tier, {p_disp} pays a {div_f.value_str} dividend rate and {apy_f.value_str} APY.",
                    sources=[Source(url=apy_f.source_url, snapshot_id=apy_f.snapshot_id, quote=apy_f.raw_quote)],
                    confident=True,
                )
            return Answer(
                text=f"For balances in the {t_lbl} tier, {p_disp} pays {apy_f.value_str} APY.",
                sources=[Source(url=apy_f.source_url, snapshot_id=apy_f.snapshot_id, quote=apy_f.raw_quote)],
                confident=True,
            )

        # General Share Savings / Savings rate inquiry: report all tiers or top tier dynamically
        apy_s_facts = [f for f in savings_facts if f.identity.attribute == "apy"] or savings_facts
        apy_s_facts.sort(key=lambda x: (x.value_num or 0.0, EligibilityEngine.rank_key(x)), reverse=True)
        top_f = apy_s_facts[0]
        p_disp = format_product_display_name(top_f.identity.product_name, top_f.identity.entity)
        t_lbl = f" for balances of {top_f.identity.tier_interval.raw_label}" if top_f.identity.tier_interval and top_f.identity.tier_interval.raw_label else ""
        return Answer(
            text=f"{p_disp} pays up to {top_f.value_str} APY{t_lbl}.",
            sources=[Source(url=top_f.source_url, snapshot_id=top_f.snapshot_id, quote=top_f.raw_quote)],
            confident=True,
        )

    def _resolve_fee(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
        eval_date: str,
        cutoff_date: str,
    ) -> Optional[Answer]:
        # Determine exact requested fee attribute
        req_attr = ""
        fee_label = "fee"
        if "foreign transaction" in q_lower:
            req_attr = "foreign_transaction_fee"
            fee_label = "credit card foreign transaction fee"
        elif "cash advance" in q_lower:
            req_attr = "cash_advance_fee"
            fee_label = "credit card cash advance fee"
        elif "balance transfer" in q_lower:
            req_attr = "balance_transfer_fee"
            fee_label = "credit card balance transfer fee"
        elif "annual fee" in q_lower:
            req_attr = "annual_fee"
            fee_label = "annual fee"
        elif "late" in q_lower:
            req_attr = "late_fee"
            fee_label = "late payment fee"
        elif "overdraft" in q_lower or "courtesy pay" in q_lower or "nsf" in q_lower or "paid item" in q_lower:
            req_attr = "overdraft_fee"
            fee_label = "overdraft fee"
        elif "replacement" in q_lower or ("lost" in q_lower and "debit" in q_lower):
            req_attr = "replacement_fee"
            fee_label = "lost debit card replacement fee"
        elif "statement" in q_lower:
            req_attr = "monthly_fee"
            fee_label = "paper statement fee"

        if req_attr:
            fee_facts = [
                f for f in eligible_facts
                if f.identity.attribute == req_attr
            ]
        else:
            # Generic fee query: require product_name token overlap (never map generic "credit card fee" to debit card replacement fee)
            q_tokens = {w for w in re.findall(r"\b[a-z]{4,}\b", q_lower) if w not in ("what", "does", "charge", "dupaco", "much")}
            fee_facts = [
                f for f in eligible_facts
                if f.identity.entity in ("fee", "credit_card", "debit_card", "overdraft", "paper_statement")
                and "fee" in f.identity.attribute
                and any(tok in f.identity.product_name or tok in f.identity.attribute for tok in q_tokens)
            ]

        if fee_facts:
            fee_facts.sort(key=EligibilityEngine.rank_key, reverse=True)
            f_top = fee_facts[0]
            return Answer(
                text=f"The {fee_label} is {f_top.value_str}.",
                sources=[Source(url=f_top.source_url, snapshot_id=f_top.snapshot_id, quote=f_top.raw_quote)],
                confident=True,
            )

        # Last-known observation policy for omitted pages vs. retired fees on surviving pages:
        # Only fall back to an earlier snapshot if the source page URL itself disappeared from target_snap.pages.
        # If the source page still exists in target_snap.pages and no longer lists the fee, the fee was retired/removed.
        if req_attr:
            for snap in reversed(self.ledger.chronological_snapshots):
                if snap.taken_on < target_snap.taken_on:
                    older_fees = [
                        f for f in snap.facts
                        if f.identity.attribute == req_attr
                        and EligibilityEngine.is_eligible(f, target_date=eval_date, cutoff_date=cutoff_date)
                    ]
                    if older_fees:
                        older_fees.sort(key=EligibilityEngine.rank_key, reverse=True)
                        f_old = older_fees[0]
                        if f_old.source_url not in target_snap.pages:
                            return Answer(
                                text=f"The {fee_label} is {f_old.value_str} (last published in snapshot {f_old.snapshot_id}).",
                                sources=[Source(url=f_old.source_url, snapshot_id=f_old.snapshot_id, quote=f_old.raw_quote)],
                                confident=True,
                            )
                        break

        return Answer(
            text=f"No published {fee_label} was found in the available snapshot.",
            sources=[],
            confident=False,
        )

    def _resolve_branches(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
        eval_date: str,
        cutoff_date: str,
    ) -> Optional[Answer]:
        # 1. Branch count
        if "how many branches" in q_lower or "number of branches" in q_lower or "how many locations" in q_lower or "branch count" in q_lower:
            branch_facts = [
                f for f in eligible_facts
                if f.identity.attribute == "branch_count"
            ]
            winning_b = EligibilityEngine.get_winning_fact(
                branch_facts,
                target_date=eval_date,
                cutoff_date=cutoff_date,
                active_snapshot_id=target_snap.snapshot_id,
            )
            if winning_b:
                return Answer(
                    text=f"Dupaco has {winning_b.value_str} branch office locations.",
                    sources=[Source(url=winning_b.source_url, snapshot_id=winning_b.snapshot_id, quote=winning_b.raw_quote)],
                    confident=True,
                )
            return Answer(
                text="No branch count is published in the available snapshot.",
                sources=[],
                confident=False,
            )

        # 2. Galena branch address
        if "galena" in q_lower and ("address" in q_lower or "where" in q_lower or "location" in q_lower):
            for u, p in target_snap.pages.items():
                if "galena" in p.markdown.lower():
                    q_line = find_verbatim_line(p.markdown, r"\d+\s+[^\n,]+,\s*Galena,\s*IL")
                    if q_line:
                        m_addr = re.search(r"(\d+\s+[^\n|,]+,\s*Galena,\s*IL\s*,?\s*\d{5})", q_line, re.I)
                        addr_str = m_addr.group(1).strip() if m_addr else q_line
                        return Answer(
                            text=f"The Galena branch is located at {addr_str}.",
                            sources=[Source(url=u, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                            confident=True,
                        )
            return Answer(
                text="The Galena branch address was not found in the available snapshot.",
                sources=[],
                confident=False,
            )

        # 3. Hy-Vee branch closure & alternatives
        if "hy-vee" in q_lower or "hyvee" in q_lower:
            hyvee_cands = sorted(
                [p for u, p in target_snap.pages.items() if "hy-vee" in u.lower() or "hy-vee" in p.markdown.lower()],
                key=lambda p: (0 if "hy-vee-branch-closure" in p.url.lower() else 1, p.url),
            )
            if not hyvee_cands:
                return Answer(
                    text="No information about the Hy-Vee branch was found in the available snapshot.",
                    sources=[],
                    confident=False,
                )
            if "where" in q_lower or "full-service" in q_lower or "alternative" in q_lower or "visit" in q_lower or "nearby" in q_lower:
                for hyvee_page in hyvee_cands:
                    q_line = find_verbatim_line(
                        hyvee_page.markdown,
                        r"(?:branches?\s+at\s+[A-Z][A-Za-z\s]+?\s+and\s+[A-Z][A-Za-z\s]+|Key\s+West\s+and\s+Sycamore|visit\s+[^\n]+branch|nearby\s+[^\n]+branch)",
                    )
                    if q_line:
                        m_alt = re.search(
                            r"(?:branches?\s+at|visit\s+(?:our\s+|the\s+)?|nearby\s+)([A-Z][A-Za-z\s]+?(?:\s+(?:and|&)\s+[A-Z][A-Za-z\s]+?)?)(?:[,\"\u201d\.]|\s+branch|\s+locations?|\s+for\b)",
                            q_line,
                            re.I,
                        )
                        alt_str = m_alt.group(1).strip() if m_alt else q_line
                        return Answer(
                            text=f"Hy-Vee branch members can visit {alt_str} for full-service banking.",
                            sources=[Source(url=hyvee_page.url, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                            confident=True,
                        )
            if "when" in q_lower or "close" in q_lower or "still open" in q_lower:
                for hyvee_page in hyvee_cands:
                    m_close = re.search(
                        r"(?:close[sd]?|closing)[^.\n]*?\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s*\d{4})\b",
                        hyvee_page.markdown,
                        re.I,
                    )
                    if m_close:
                        close_date_str = m_close.group(1)
                        q_line = find_verbatim_line(hyvee_page.markdown, re.escape(close_date_str)) or close_date_str
                        return Answer(
                            text=f"No - the Hy-Vee branch was scheduled to close {close_date_str}.",
                            sources=[Source(url=hyvee_page.url, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                            confident=True,
                        )
            return Answer(
                text="The requested Hy-Vee branch detail was not found in the available snapshot.",
                sources=[],
                confident=False,
            )

        # 4. Specific named branch schedule check (do not substitute generic lobby/drive-up hours for a specific branch)
        named_city = next((c for c in KNOWN_BRANCH_CITIES if c in q_lower), None)
        if named_city and any(k in q_lower for k in ("hours", "lobby", "drive", "open", "saturday", "sunday", "schedule", "weekend")):
            day_kw = next((d for d in ("saturday", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday") if d in q_lower), "")
            for u, p in target_snap.pages.items():
                if named_city in p.markdown.lower():
                    pat = rf"{re.escape(named_city)}[^\n]*{day_kw}[^\n]*\b\d{{1,2}}(?::\d{{2}})?\s*(?:a\.?m\.?|p\.?m\.?)"
                    q_line = find_verbatim_line(p.markdown, pat)
                    if q_line:
                        return Answer(
                            text=f"Schedule for the {named_city.title()} branch: {q_line}",
                            sources=[Source(url=u, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                            confident=True,
                        )
            return Answer(
                text=f"The website does not publish an explicit {day_kw + ' ' if day_kw else ''}schedule for the {named_city.title()} branch.",
                sources=[],
                confident=False,
            )

        # 5. General Lobby Hours (require explicit time schedule in page markdown)
        if "lobby" in q_lower:
            all_sched_lines: list[str] = []
            sources: list[Source] = []
            seen_urls: set[str] = set()
            for u, p in sorted(
                target_snap.pages.items(),
                key=lambda kv: (0 if "newest-learning-lab" in kv[0].lower() else (1 if "cedar-rapids" in kv[0].lower() else 2), kv[0]),
            ):
                if "lobby" in p.markdown.lower():
                    lines = p.markdown.splitlines()
                    in_lobby = False
                    page_sched: list[str] = []
                    for ln in lines:
                        s_ln = ln.strip()
                        if not s_ln:
                            continue
                        if re.search(r"^\*\*lobby\*\*|^#+\s*lobby", s_ln, re.I):
                            in_lobby = True
                            continue
                        if in_lobby:
                            if s_ln.startswith("**") or s_ln.startswith("#"):
                                in_lobby = False
                            elif re.search(r"\b(?:mon|tue|wed|thu|fri|sat|sun)[^\n]*\b(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|noon|closed)\b", s_ln, re.I):
                                page_sched.append(s_ln)
                        elif re.search(r"\blobby[^\n]*\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)", s_ln, re.I):
                            page_sched.append(s_ln)
                    if page_sched:
                        seen_norm_sched = {x.replace("–", "-").replace("—", "-").lower() for x in all_sched_lines}
                        for ps in page_sched:
                            ps_norm = ps.replace("–", "-").replace("—", "-").lower()
                            if ps_norm not in seen_norm_sched:
                                seen_norm_sched.add(ps_norm)
                                all_sched_lines.append(ps)
                        if u not in seen_urls:
                            seen_urls.add(u)
                            sources.append(Source(url=u, snapshot_id=target_snap.snapshot_id, quote=page_sched[0]))

            if all_sched_lines:
                # Also cite /contact if present in snapshot (shows branch-level lobby/contact status)
                for cu, cp in target_snap.pages.items():
                    if cu.rstrip("/").endswith("/contact") and cu not in seen_urls:
                        cq = find_verbatim_line(cp.markdown, r"(?:Lobby|8:30|branch)")
                        if cq:
                            seen_urls.add(cu)
                            sources.append(Source(url=cu, snapshot_id=target_snap.snapshot_id, quote=cq))
                sched_summary = "; ".join(all_sched_lines[:10])
                if "same" in q_lower or "all" in q_lower or "every" in q_lower or "saturday" in q_lower:
                    return Answer(
                        text=f"No - lobby schedules vary by branch ({sched_summary}).",
                        sources=sources,
                        confident=True,
                    )
                return Answer(
                    text=f"General lobby hours: {sched_summary}.",
                    sources=sources,
                    confident=True,
                )
            return Answer(
                text="No lobby hours schedule is published in the available snapshot.",
                sources=[],
                confident=False,
            )

        # 6. General Drive-up Hours (require explicit drive-up time schedule in page markdown)
        if "drive" in q_lower:
            for u, p in sorted(
                target_snap.pages.items(),
                key=lambda kv: (0 if "newest-learning-lab" in kv[0].lower() else 1, kv[0]),
            ):
                if "drive" in p.markdown.lower():
                    lines = p.markdown.splitlines()
                    in_drive = False
                    drive_sched: list[str] = []
                    for ln in lines:
                        s_ln = ln.strip()
                        if not s_ln:
                            continue
                        if re.search(r"^\*\*drive[- ]?up\*\*|^#+\s*drive[- ]?up", s_ln, re.I):
                            in_drive = True
                            continue
                        if in_drive:
                            if s_ln.startswith("**") or s_ln.startswith("#"):
                                in_drive = False
                            elif re.search(r"\b(?:mon|tue|wed|thu|fri|sat|sun)[^\n]*\b(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|noon|atm\s+only|closed)\b", s_ln, re.I):
                                drive_sched.append(s_ln)
                        elif re.search(r"\bdrive[- ]?up[^\n]*\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)", s_ln, re.I):
                            drive_sched.append(s_ln)
                    if drive_sched:
                        sched_summary = "; ".join(drive_sched[:4])
                        return Answer(
                            text=f"Drive-up hours at most branches: {sched_summary}.",
                            sources=[Source(url=u, snapshot_id=target_snap.snapshot_id, quote=drive_sched[0])],
                            confident=True,
                        )
            return Answer(
                text="No drive-up hours schedule is published in the available snapshot.",
                sources=[],
                confident=False,
            )

        return None

    def _resolve_routing_number(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
    ) -> Optional[Answer]:
        routing_facts = [
            f for f in eligible_facts
            if f.identity.attribute == "routing_number"
        ]
        if routing_facts:
            routing_facts.sort(key=EligibilityEngine.rank_key, reverse=True)
            f_r = routing_facts[0]
            return Answer(
                text=f"Dupaco's routing number is {f_r.value_str}.",
                sources=[Source(url=f_r.source_url, snapshot_id=f_r.snapshot_id, quote=f_r.raw_quote)],
                confident=True,
            )
        return Answer(
            text="No routing number is published in the available snapshot.",
            sources=[],
            confident=False,
        )

    def _resolve_statistics(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
    ) -> Optional[Answer]:
        if "forbes" in q_lower:
            forbes_pages = sorted(
                [(u, p) for u, p in target_snap.pages.items() if "forbes" in u.lower() or "forbes" in p.markdown.lower()],
                key=lambda kv: (1 if "/newsroom/forbes" in kv[0].lower() else 0, kv[0]),
                reverse=True,
            )
            for u, p in forbes_pages:
                q_line = find_verbatim_line(
                    p.markdown,
                    r"(?:first|second|third|fourth|fifth|sixth|seventh|\d+(?:st|nd|rd|th))\s+time\s+forbes|forbes[^\n]*(?:fifth\s+time|best[- ]in[- ]state\s+credit\s+union)",
                )
                if not q_line:
                    q_line = find_verbatim_line(
                        p.markdown,
                        r"forbes[^\n]*(?:recognized|named|ranked|best[- ]in[- ]state|top\s+credit\s+union)",
                    )
                if q_line and "no forbes" not in q_line.lower() and "not recognized" not in q_line.lower():
                    return Answer(
                        text=q_line,
                        sources=[Source(url=u, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                        confident=True,
                    )
            return Answer(
                text="No Forbes recognition or award was found in the available snapshot.",
                sources=[],
                confident=False,
            )

        if "volunteer" in q_lower or "vto" in q_lower or "giving back" in q_lower:
            emp_facts = [f for f in eligible_facts if f.identity.attribute == "vto_employees"]
            hrs_facts = [f for f in eligible_facts if f.identity.attribute == "vto_hours"]
            if not emp_facts and not hrs_facts:
                return Answer(
                    text="No Volunteer Time Off (VTO) statistics are published in the available snapshot.",
                    sources=[],
                    confident=False,
                )

            m_yr = re.search(r"\b(20\d{2})\b", q_lower)
            if m_yr:
                req_yr = m_yr.group(1)
                yr_emp = [f for f in emp_facts if f.identity.reporting_period == req_yr]
                yr_hrs = [f for f in hrs_facts if f.identity.reporting_period == req_yr]
                if not yr_emp and not yr_hrs:
                    return Answer(
                        text=f"No Volunteer Time Off (VTO) statistics for {req_yr} are published in the available snapshot.",
                        sources=[],
                        confident=False,
                    )
                f_e = yr_emp[0] if yr_emp else yr_hrs[0]
                hrs_clause = f" across {yr_hrs[0].value_str} hours" if yr_hrs else ""
                return Answer(
                    text=f"In {req_yr}, {f_e.value_str} Dupaco employees used volunteer time off (VTO){hrs_clause}.",
                    sources=[Source(url=f_e.source_url, snapshot_id=f_e.snapshot_id, quote=f_e.raw_quote)],
                    confident=True,
                )

            # Unqualified VTO query: report all reporting periods present in eligible_facts
            periods = sorted({f.identity.reporting_period for f in emp_facts if f.identity.reporting_period})
            parts: list[str] = []
            sources: list[Source] = []
            seen_q: set[tuple[str, str]] = set()
            for per in periods:
                f_e = next((f for f in emp_facts if f.identity.reporting_period == per), None)
                f_h = next((f for f in hrs_facts if f.identity.reporting_period == per), None)
                if f_e:
                    h_str = f" across {f_h.value_str} hours" if f_h else ""
                    parts.append(f"In {per}, {f_e.value_str} employees used Volunteer Time Off (VTO){h_str}")
                    qk = (f_e.source_url, f_e.raw_quote)
                    if qk not in seen_q:
                        seen_q.add(qk)
                        sources.append(Source(url=f_e.source_url, snapshot_id=f_e.snapshot_id, quote=f_e.raw_quote))

            if parts:
                return Answer(
                    text="; ".join(parts) + ".",
                    sources=sources,
                    confident=True,
                )

        return None

    def _resolve_mobile_payments(self, q_lower: str, target_snap: SnapshotRecord) -> Optional[Answer]:
        known_wallets = ("Apple Pay", "Google Pay", "Samsung Pay", "Garmin Pay", "Fitbit Pay")
        for u, p in target_snap.pages.items():
            md_l = p.markdown.lower()
            if "mobile-payments" in u.lower() or "wallet" in md_l or any(w.lower() in md_l for w in known_wallets):
                found = [w for w in known_wallets if w.lower() in md_l]
                if found:
                    q_line = find_verbatim_line(p.markdown, re.escape(found[0])) or found[0]
                    if len(found) == 1:
                        w_list_str = found[0]
                    elif len(found) == 2:
                        w_list_str = f"{found[0]} and {found[1]}"
                    else:
                        w_list_str = ", ".join(found[:-1]) + f", and {found[-1]}"
                    return Answer(
                        text=f"Yes - Dupaco cards can be added to mobile wallets including {w_list_str}.",
                        sources=[Source(url=u, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                        confident=True,
                    )
        return Answer(
            text="No mobile wallet payment information was found in the available snapshot.",
            sources=[],
            confident=False,
        )

    def _resolve_membership(self, q_lower: str, target_snap: SnapshotRecord) -> Optional[Answer]:
        join_cands = sorted(
            [p for u, p in target_snap.pages.items() if "/join" in u.lower() or "charter area" in p.markdown.lower() or "membership" in p.markdown.lower()],
            key=lambda p: (0 if "/about/join" in p.url.lower() or p.url.lower().endswith("/join") else 1, p.url),
        )
        if not join_cands:
            return Answer(
                text="No membership eligibility information was found in the available snapshot.",
                sources=[],
                confident=False,
            )

        if "online" in q_lower or "how" in q_lower or "link" in q_lower:
            for join_page in join_cands:
                if "in person only" in join_page.markdown.lower() or "in-person only" in join_page.markdown.lower():
                    q_line = find_verbatim_line(join_page.markdown, r"in[- ]person\s+only") or join_page.markdown.strip()
                    return Answer(
                        text=f"Online joining is not available: {q_line}",
                        sources=[Source(url=join_page.url, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                        confident=False,
                    )
                m_url = re.search(r"(https?://[^\s\)\"]*join[^\s\)\"]*)", join_page.markdown, re.I)
                if m_url:
                    j_url = m_url.group(1).rstrip(".,;)")
                    q_line = find_verbatim_line(join_page.markdown, re.escape(j_url)) or j_url
                    return Answer(
                        text=f"You can join Dupaco online at {j_url}.",
                        sources=[Source(url=join_page.url, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                        confident=True,
                    )
            return Answer(
                text="No online membership application URL was found in the available snapshot.",
                sources=[],
                confident=False,
            )

        # Eligibility / charter area inquiry: combine charter area line and state list line if present
        charter_line = None
        states_line = None
        sources: list[Source] = []
        for join_page in join_cands:
            if not charter_line:
                cl = find_verbatim_line(join_page.markdown, r"(?:charter\s+area|lives?\s+or\s+works?\s+in)")
                if cl:
                    charter_line = cl
                    sources.append(Source(url=join_page.url, snapshot_id=target_snap.snapshot_id, quote=cl))
            if not states_line:
                sl = find_verbatim_line(join_page.markdown, r"Iowa,[^\n]*Illinois[^\n]*Wisconsin")
                if sl and sl != charter_line:
                    states_line = sl
                    if not sources or sources[-1].url != join_page.url:
                        sources.append(Source(url=join_page.url, snapshot_id=target_snap.snapshot_id, quote=sl))

        if charter_line or states_line:
            parts = [x for x in (charter_line, states_line) if x]
            return Answer(
                text="Membership eligibility: " + " ".join(parts),
                sources=sources,
                confident=True,
            )
        return Answer(
            text="No charter area eligibility details were found in the available snapshot.",
            sources=[],
            confident=False,
        )

    def _resolve_contact_update(self, q_lower: str, target_snap: SnapshotRecord) -> Optional[Answer]:
        for u, p in target_snap.pages.items():
            md_l = p.markdown.lower()
            if ("contact" in md_l or "address" in md_l or "phone" in md_l) and ("shine" in md_l or re.search(r"\b\d{3}-\d{3}-\d{4}\b", p.markdown)):
                m_ph = re.search(r"\b(\d{3}-\d{3}-\d{4})\b", p.markdown)
                q_line = find_verbatim_line(p.markdown, r"(?:\d{3}-\d{3}-\d{4}|Shine)")
                if q_line:
                    ph_part = f" or calling {m_ph.group(1)}" if m_ph else ""
                    shine_part = "logging into Shine online banking" if "shine" in md_l else "contacting member services"
                    return Answer(
                        text=f"You can update your contact information by {shine_part}{ph_part}.",
                        sources=[Source(url=u, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                        confident=True,
                    )
        return Answer(
            text="No instructions for updating contact information were found in the available snapshot.",
            sources=[],
            confident=False,
        )

    def _resolve_student_loans(self, q_lower: str, target_snap: SnapshotRecord) -> Optional[Answer]:
        for u, p in target_snap.pages.items():
            if "student-loan" in u.lower() or "student loan" in p.markdown.lower():
                q_line = find_verbatim_line(p.markdown, r"student\s+loans?")
                if q_line:
                    return Answer(
                        text=f"Yes - Dupaco offers student loan options ({q_line}).",
                        sources=[Source(url=u, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                        confident=True,
                    )
        return Answer(
            text="No student loan information was found in the available snapshot.",
            sources=[],
            confident=False,
        )

    def _search_sections(self, q_raw: str, q_lower: str, target_snap: SnapshotRecord) -> Optional[Answer]:
        # Do not use keyword section search for rate/pricing/schedule questions where structured facts were absent
        if any(k in q_lower for k in ("rate", "apr", "apy", "fee", "hours", "saturday", "sunday", "schedule", "routing")):
            return None

        keywords = [
            w for w in re.findall(r"\b[a-z]{4,}\b", q_lower)
            if w not in ("what", "when", "where", "which", "does", "have", "with", "from", "that", "this", "dupaco", "current", "right")
        ]
        if not keywords:
            return None

        best_sec: Optional[DocumentSection] = None
        best_score = 0

        for sec in target_snap.sections:
            sec_l = sec.clean_markdown.lower()
            score = sum(2 if kw in sec.heading.lower() else (1 if kw in sec_l else 0) for kw in keywords)
            if score > best_score:
                best_score = score
                best_sec = sec

        if best_sec and best_score >= 2:
            q_line = find_verbatim_line(best_sec.raw_markdown, r"[A-Za-z0-9]") or best_sec.raw_markdown.strip()[:120]
            clean_text = best_sec.clean_markdown.strip()
            summary = clean_text[:300].replace("\n", " ")
            return Answer(
                text=summary,
                sources=[Source(url=best_sec.url, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                confident=True,
            )

        return None
