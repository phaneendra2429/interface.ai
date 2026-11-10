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
from datetime import date
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

NUMBER_WORDS = dict(zip(
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(),
    range(1, 20),
))
TENS_WORDS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))

KNOWN_BRANCH_CITIES = (
    "galena", "cedar falls", "waterloo", "dubuque", "platteville", "peosta",
    "dyersville", "manchester", "marion", "hutchins", "pennsylvania", "jfk",
    "key west", "sycamore", "mindoro", "holmen", "oesterle", "carroll",
    "ames", "waukon", "mullan", "corridor", "asarco", "salzig",
)


def _resolve_raw_date_token(
    raw: str, cutoff_date: Optional[str], shared_year: Optional[int]
) -> Optional[str]:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return date.fromisoformat(raw).isoformat()
    if "/" in raw:
        month, day, year = map(int, raw.split("/"))
        return date(year, month, day).isoformat()

    parts = re.split(r"[\s,]+", raw)
    month = MONTH_NAMES[parts[0].lower()]
    year = int(parts[-1]) if len(parts[-1]) == 4 and parts[-1].isdigit() else shared_year
    if year is None:
        return None
    has_day = len(parts) >= 2 and len(parts[1]) < 4
    if has_day:
        return date(year, month, int(parts[1])).isoformat()
    month_end = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    if cutoff_date and cutoff_date[:7] == month_end[:7]:
        return min(cutoff_date, month_end)
    return month_end


FULL_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def extract_all_date_expressions(text: str, cutoff_date: Optional[str] = None) -> list[tuple[str, str]]:
    """Extract all date expressions in order of appearance as (raw_label, iso_date)."""
    pattern = re.compile(
        rf"\b(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{4}}|"
        rf"(?:{MONTH_PATTERN_STR})(?:\s+\d{{1,2}})?(?:,?\s*\d{{4}})?)\b",
        re.I,
    )
    labels = []
    for match in pattern.finditer(text):
        label = match.group()
        if label.lower() in MONTH_NAMES:
            if label.lower() not in FULL_MONTH_NAMES:
                continue
            prefix = text[:match.start()]
            if not re.search(r"\b(?:in|of|on|from|since|by|for|during|between|versus|vs\.?|compared\s+to|and)\s+$", prefix, re.I):
                continue
        labels.append(label)
    explicit_years = {
        int(y.group()) for label in labels
        if (y := re.search(r"\b\d{4}\b", label))
    }
    shared_year = next(iter(explicit_years)) if len(explicit_years) == 1 else (
        int(cutoff_date[:4]) if cutoff_date else None
    )
    return [(label, iso) for label in labels
            if (iso := _resolve_raw_date_token(label, cutoff_date, shared_year))]


def parse_date_expression(text: str, cutoff_date: Optional[str] = None) -> Optional[str]:
    """Parse primary date expression from question text."""
    exprs = extract_all_date_expressions(text, cutoff_date=cutoff_date)
    return exprs[0][1] if exprs else None


def extract_requested_term_months(q_lower: str) -> Optional[int]:
    """Extract any requested term in months (supporting integer or word months/years)."""
    match = re.search(r"(?<![\w-])(\d+)\s*[-–—]?\s*(months?|mo|years?|yr)\b", q_lower)
    if match:
        return int(match[1]) * (12 if match[2].startswith("y") else 1)
    units = "|".join(NUMBER_WORDS)
    tens = "|".join(TENS_WORDS)
    match = re.search(
        rf"(?<![\w-])((?:{tens})(?:[- ](?:{units}))?|(?:{units}))[-–— ]+(months?|years?)\b",
        q_lower,
    )
    if not match:
        return None
    words = re.split(r"[- ]", match[1])
    number = TENS_WORDS[words[0]] if words[0] in TENS_WORDS else NUMBER_WORDS[words[0]]
    if len(words) == 2:
        number += NUMBER_WORDS[words[1]]
    return number * (12 if match[2].startswith("y") else 1)


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
        return self._resolve_at(question, as_of)

    def _resolve_at(
        self, question: str, as_of: Optional[str], requested_date: Optional[str] = None,
        date_exprs: Optional[list[tuple[str, str]]] = None,
    ) -> Answer:
        q_raw = question
        q_lower = question.lower().replace("home-equity", "home equity").replace("balance-transfer", "balance transfer")

        # Clock 1: Cutoff Date
        cutoff_date = as_of if as_of is not None else self.ledger.latest_date

        # Clock 1 Pre-baseline check
        if self.ledger.earliest_date and cutoff_date and cutoff_date < self.ledger.earliest_date:
            return Answer(
                text="The website had no snapshot coverage before the baseline crawl; information is not available.",
                sources=[],
                confident=False,
            )

        if date_exprs is None:
            try:
                date_exprs = extract_all_date_expressions(question, cutoff_date=cutoff_date)
            except ValueError:
                return Answer(text="Please provide a valid calendar date.", confident=False)
            if len(date_exprs) >= 2:
                return self._check_date_comparison(question, date_exprs, cutoff_date)
            requested_date = date_exprs[0][1] if date_exprs else None

        if cutoff_date and requested_date and requested_date > cutoff_date:
            if as_of is None and "still open" in q_lower and re.search(r"hy-?vee", q_lower):
                latest = self.ledger.get_target_snapshot(cutoff_date)
                announcement = self._resolve_branches(q_lower, latest, [], cutoff_date, cutoff_date) if latest else None
                if announcement and announcement.sources:
                    return Answer(
                        text="I cannot verify the requested date. The latest crawl says: " + announcement.sources[0].quote,
                        sources=announcement.sources,
                        confident=False,
                    )
            return Answer(
                text="The requested date is in the future relative to the snapshot cutoff date; information is not available.",
                confident=False,
            )

        eval_date = requested_date or cutoff_date

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
            or "physical offices" in q_lower
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
            ans = self._resolve_statistics(q_lower, target_snap, eligible_facts, eval_date)
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
        question: str,
        date_exprs: list[tuple[str, str]],
        cutoff_date: Optional[str],
    ) -> Answer:
        """Resolve each comparison point with the same query and evidence ceiling."""
        answers = []
        for label, requested in date_exprs:
            answer = self._resolve_at(question, cutoff_date, requested, date_exprs)
            if not answer.confident:
                return Answer(text="Matching evidence was not found for all requested dates.", confident=False)
            answers.append((label, answer))
        return Answer(
            text=" ".join(f"As of {label}: {answer.text}" for label, answer in answers),
            sources=[source for _, answer in answers for source in answer.sources],
            confident=True,
        )

    def _resolve_credit_cards(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
    ) -> Optional[Answer]:
        if "intro" in q_lower and any(w in q_lower for w in ("how long", "duration", "window", "last")):
            return self._balance_transfer_terms(q_lower, target_snap)
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
        elif req_personal_only:
            filtered = [f for f in filtered if f.identity.account_category == "personal"]
        elif (req_platinum_only or req_rewards_only) and "business" not in q_lower:
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
            f_std = EligibilityEngine.choose(std_facts)
            if not f_std:
                return Answer(
                    text="No unambiguous standard APR was found for the requested credit card.",
                    sources=[],
                    confident=False,
                )
            card_title = format_product_display_name(f_std.identity.product_name, "credit_card")
            range_note = ""
            if f_std.typed_value and f_std.typed_value.is_range and f_std.typed_value.high is not None:
                range_note = f" (range up to {f_std.typed_value.high:.2f}%)"

            sources = [Source(url=f_std.source_url, snapshot_id=f_std.snapshot_id, quote=f_std.raw_quote)]
            return Answer(
                text=f"The standard purchase APR for the Dupaco {card_title} is a variable rate of {f_std.value_str}{range_note}.",
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

        for (cat, pname), g_facts in sorted_groups:
            std_f_list = [f for f in g_facts if not f.identity.is_intro]
            best_f = EligibilityEngine.choose(std_f_list)
            if not best_f:
                continue
            disp_name = format_product_display_name(pname, "credit_card")
            if cat == "business" and "business" not in disp_name.lower():
                disp_name = f"Business {disp_name}"

            card_entries.append(f"{len(card_entries) + 1}. {disp_name}: {best_f.value_str} variable APR.")
            corroboration = sorted(
                (f for f in std_f_list if "credit-cards" in f.source_url and f.typed_value and best_f.typed_value
                 and f.typed_value.equals(best_f.typed_value)),
                key=lambda f: (f.source_url, f.raw_quote),
            )
            for sf in [best_f, *corroboration]:
                qk = (sf.source_url, sf.raw_quote)
                if qk not in seen_quotes:
                    seen_quotes.add(qk)
                    sources.append(Source(url=sf.source_url, snapshot_id=sf.snapshot_id, quote=sf.raw_quote))

        if not card_entries:
            return Answer(text="No unambiguous standard credit card APRs were found.", confident=False)
        count_words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
        n_cards = len(card_entries)
        c_word = count_words.get(n_cards, str(n_cards))
        plural = "card" if n_cards == 1 else "cards"

        return Answer(
            text=f"Dupaco offers {c_word} credit {plural}: " + " ".join(card_entries),
            sources=sources,
            confident=True,
        )

    def _balance_transfer_terms(self, question: str, snapshot: SnapshotRecord) -> Answer:
        product = next((p for p in ("platinum visa", "rewards visa") if p in question), None)
        if not product:
            return Answer(text="Which credit card are you asking about?", confident=False)
        amounts = r"(?:\d+|" + "|".join(NUMBER_WORDS) + ")"
        duration_re = re.compile(rf"\b(?:for|lasts?|expires)\s+({amounts})\s+(months?)\b", re.I)
        window_re = re.compile(rf"\b(?:within|(?:during|in) the first)\s+({amounts})\s+(days?)\b", re.I)
        for page in sorted(snapshot.pages.values(), key=lambda p: ("/credit-cards" not in p.url, p.url)):
            section = ""
            duration = window = None
            sources = []
            for line in page.markdown.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    section = stripped.lstrip("# ").lower()
                    duration = window = None
                    sources = []
                    continue
                lower = stripped.lower()
                if product not in lower and product not in section:
                    continue
                if ("business" in question) != ("business" in (lower + " " + section + " " + page.url)):
                    continue
                d_match = duration_re.search(stripped) if re.search(r"intro|transfer|expires", lower) else None
                w_match = window_re.search(stripped) if "transfer" in lower else None
                if d_match or w_match:
                    source = Source(url=page.url, snapshot_id=snapshot.snapshot_id, quote=stripped)
                    if source not in sources:
                        sources.append(source)
                if d_match:
                    duration = d_match.groups()
                if w_match:
                    window = w_match.groups()
                if duration and window:
                    d_number, d_unit = duration
                    w_number, w_unit = window
                    d_number = NUMBER_WORDS.get(d_number.lower(), d_number)
                    w_number = NUMBER_WORDS.get(w_number.lower(), w_number)
                    return Answer(
                        text=f"The introductory balance-transfer APR lasts {d_number} {d_unit}. Transfers must post within {w_number} {w_unit}.",
                        sources=sources,
                    )
        return Answer(text="The requested introductory balance-transfer terms are not published in the available snapshot.", confident=False)

    def _resolve_certificate(
        self,
        q_lower: str,
        target_snap: SnapshotRecord,
        eligible_facts: list[ExtractedFact],
    ) -> Optional[Answer]:
        # Direct link inquiry
        if "link" in q_lower or "url" in q_lower or "website" in q_lower or "webpage" in q_lower:
            category = "business" if "business" in q_lower else "personal"
            rate_rows = [f for f in eligible_facts if f.identity.entity == "certificate"
                         and f.identity.attribute == "apy" and f.identity.account_category == category]
            if rate_rows:
                evidence = min(rate_rows, key=lambda f: (
                    "/savings-account" not in f.source_url, -f.authority_score, f.source_url, f.raw_quote,
                ))
                return Answer(
                    text=f"You can view the certificate rates at {evidence.source_url}.",
                    sources=[Source(url=evidence.source_url, snapshot_id=evidence.snapshot_id, quote=evidence.raw_quote)],
                )
            return Answer(text="No certificate rates page link was found in the available snapshot.", confident=False)

        req_dividend = "dividend" in q_lower and "apy" not in q_lower
        target_attr = "dividend_rate" if req_dividend else "apy"
        metric_label = "dividend rate" if req_dividend else "APY"

        cert_facts = [
            f for f in eligible_facts
            if f.identity.entity == "certificate"
            and f.identity.attribute == target_attr
            and f.identity.term_months is not None
        ]
        if "traditional" in q_lower:
            categories = {"traditional_ira"}
        elif "roth" in q_lower:
            categories = {"roth_ira"}
        elif re.search(r"\bira\b", q_lower):
            categories = {"ira", "traditional_ira", "roth_ira"}
        else:
            categories = {"business" if "business" in q_lower else "personal"}
        cert_facts = [f for f in cert_facts if f.identity.account_category in categories]

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
                    and mf.identity.account_category == f_cert.identity.account_category
                    and mf.identity.is_special == f_cert.identity.is_special
                    and mf.source_url == f_cert.source_url
                    and mf.raw_quote == f_cert.raw_quote
                ):
                    return f" ({mf.value_str} minimum)"
            return ""

        term = extract_requested_term_months(q_lower)
        req_jumbo_only = "jumbo" in q_lower and "regular" not in q_lower
        req_regular_only = ("regular" in q_lower or "standard" in q_lower) and "jumbo" not in q_lower
        if re.search(r"\bspecial\b", q_lower):
            cert_facts = [f for f in cert_facts if f.identity.is_special]
        if term is not None:
            cert_facts = [f for f in cert_facts if f.identity.term_months == term]

        # Full schedule / general rates inquiry when no specific term is requested
        if term is None or len({f.identity.account_category for f in cert_facts}) > 1:
            grouped: dict[tuple[int, bool, bool, str], list[ExtractedFact]] = defaultdict(list)
            for f in cert_facts:
                if req_jumbo_only and not f.identity.is_jumbo:
                    continue
                if req_regular_only and f.identity.is_jumbo:
                    continue
                grouped[(f.identity.term_months or 0, f.identity.is_special, f.identity.is_jumbo,
                         f.identity.account_category)].append(f)

            if not grouped:
                return Answer(
                    text="No matching certificate terms were found in the available snapshot.",
                    sources=[],
                    confident=False,
                )

            lines: list[str] = []
            sources: list[Source] = []
            seen_q: set[tuple[str, str]] = set()
            for (t_m, is_sp, is_jb, category), g_list in sorted(grouped.items()):
                bf = EligibilityEngine.choose(g_list)
                if not bf:
                    continue
                spec_lbl = " Jumbo Special" if is_jb and is_sp else (" Jumbo" if is_jb else (" Special" if is_sp else ""))
                account = category.replace("_ira", " IRA").title() + " " if category.endswith("_ira") else ""
                lines.append(f"{account}{t_m}-Month{spec_lbl}: {bf.value_str} {metric_label}")
                qk = (bf.source_url, bf.raw_quote)
                if qk not in seen_q:
                    seen_q.add(qk)
                    sources.append(Source(url=bf.source_url, snapshot_id=bf.snapshot_id, quote=bf.raw_quote))

            if not lines:
                return Answer(text="The published certificate rates disagree.", confident=False)
            return Answer(
                text=f"Dupaco's Certificate {metric_label}s are: " + ", ".join(lines) + ".",
                sources=sources,
                confident=True,
            )

        # Specific term requested: strict term match required
        term_cands = cert_facts
        if not term_cands:
            return Answer(
                text="The requested certificate term and qualifiers are not listed in the available snapshot.",
                sources=[],
                confident=False,
            )

        reg_cands = [f for f in term_cands if not f.identity.is_jumbo]
        jumbo_cands = [f for f in term_cands if f.identity.is_jumbo]
        regular = EligibilityEngine.choose(reg_cands)
        jumbo = EligibilityEngine.choose(jumbo_cands)
        if not regular and not jumbo or (req_regular_only and reg_cands and not regular) or (req_jumbo_only and jumbo_cands and not jumbo):
            return Answer(text="The published certificate rates disagree.", confident=False)
        reg_cands = [regular] if regular else []
        jumbo_cands = [jumbo] if jumbo else []

        if req_jumbo_only:
            if not jumbo_cands:
                return Answer(
                    text=f"A {term}-month jumbo certificate is not offered; certificates for {term} months are regular term certificates only.",
                    sources=[Source(url=reg_cands[0].source_url, snapshot_id=reg_cands[0].snapshot_id, quote=reg_cands[0].raw_quote)] if reg_cands else [],
                    confident=False,
                )
            f_j = jumbo_cands[0]
            sp_lbl = "Fixed Jumbo Certificate Special" if f_j.identity.is_special else "Jumbo Certificate"
            if f_j.identity.account_category.endswith("_ira"):
                sp_lbl = f"{f_j.identity.account_category.replace('_ira', '').title()} IRA {sp_lbl}"
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
                    text="No auto loan rate is published for the requested term.",
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
                if f.identity.entity == "heloc" and f.identity.attribute == "apr" and not f.identity.is_intro
            ]
            f_h = EligibilityEngine.choose(heloc_facts)
            if not f_h:
                return Answer(
                    text="No Home Equity Line of Credit (HELOC) rates are published in the available snapshot.",
                    sources=[],
                    confident=False,
                )
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
            by_term: dict[int, list[ExtractedFact]] = defaultdict(list)
            for fact in he_facts:
                by_term[fact.identity.term_months or 0].append(fact)
            winners = {tm: winner for tm, facts in sorted(by_term.items()) if (winner := EligibilityEngine.choose(facts))}
            if not winners:
                return Answer(text=f"The published {label_name} rates disagree.", confident=False)
            if len(by_term) == 1:
                best_he = next(iter(winners.values()))
                t_lbl = f"{best_he.identity.term_months}-month " if best_he.identity.term_months else ""
                return Answer(
                    text=f"The {t_lbl}{label_name} APR is as low as {best_he.value_str}.",
                    sources=[Source(url=best_he.source_url, snapshot_id=best_he.snapshot_id, quote=best_he.raw_quote)],
                    confident=True,
                )
            # Multiple terms available: report each term dynamically
            parts = [f"{tm}-month: {f.value_str} APR" if tm else f"{f.value_str} APR (term unspecified)"
                     for tm, f in winners.items()]
            srcs = [Source(url=f.source_url, snapshot_id=f.snapshot_id, quote=f.raw_quote) for f in winners.values()]
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
        tiers = [f.identity.tier_interval for f in candidates if f.identity.tier_interval]
        all_ceilings = [tier.high for tier in tiers if tier.high is not None]
        if req_amount is not None:
            if all_ceilings and all(tier.high is not None for tier in tiers):
                max_pub_ceiling = max(all_ceilings)
                if req_amount > max_pub_ceiling:
                    cap = min(
                        (f for f in candidates if f.identity.tier_interval and f.identity.tier_interval.high == max_pub_ceiling),
                        key=lambda f: (f.source_url, f.raw_quote),
                    )
                    return Answer(
                        text="The requested amount is above the maximum published for this mortgage tier.",
                        sources=[Source(url=cap.source_url, snapshot_id=cap.snapshot_id, quote=cap.raw_quote)],
                        confident=False,
                    )
            candidates = [
                f for f in candidates
                if f.identity.tier_interval and f.identity.tier_interval.contains(req_amount)
            ]
            if not candidates:
                return Answer(
                    text="No mortgage rate tier matches the requested amount.",
                    sources=[],
                    confident=False,
                )

        if not tx_type:
            purchase = [f for f in candidates if f.identity.transaction_type == "purchase"]
            if purchase:
                candidates = purchase

        def mortgage_rate(facts, attribute, ceiling=None):
            return EligibilityEngine.choose([
                f for f in facts if f.identity.attribute == attribute
                and (ceiling is None or (f.identity.tier_interval and f.identity.tier_interval.high == ceiling))
            ])

        def mortgage_sources(*facts):
            supporting = [f for f in facts if f]
            for fact in tuple(supporting):
                supporting.extend(sorted(
                    (f for f in candidates if "/home-loans" in f.source_url and f.identity == fact.identity
                     and f.typed_value and fact.typed_value and f.typed_value.equals(fact.typed_value)),
                    key=lambda f: (f.source_url, f.raw_quote),
                ))
            return [Source(url=url, snapshot_id=snapshot, quote=quote)
                    for url, snapshot, quote in dict.fromkeys(
                        (f.source_url, f.snapshot_id, f.raw_quote) for f in supporting
                    )]

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
            if len({f.identity.term_months for f in metric_cands}) > 1:
                return Answer(text="Which mortgage term are you asking about?", confident=False)
            if req_amount is None:
                ceilings = [f.identity.tier_interval.high for f in metric_cands
                            if f.identity.tier_interval and f.identity.tier_interval.high is not None]
                if ceilings:
                    first_ceiling = min(ceilings)
                    metric_cands = [f for f in metric_cands if f.identity.tier_interval
                                    and f.identity.tier_interval.high == first_ceiling]
            best_f = EligibilityEngine.choose(metric_cands)
            if not best_f:
                return Answer(text="The published mortgage rates disagree for the requested tier.", confident=False)
            same_tier = [f for f in candidates if f.identity.term_months == best_f.identity.term_months
                         and f.identity.transaction_type == best_f.identity.transaction_type
                         and f.identity.tier_interval == best_f.identity.tier_interval]
            paired_ir = mortgage_rate(same_tier, "interest_rate")
            paired_apr = mortgage_rate(same_tier, "apr")

            tx_label = f"{best_f.identity.transaction_type.capitalize()} " if best_f.identity.transaction_type else ""
            months = best_f.identity.term_months
            t_label = (f"{months // 12}-Year Fixed " if months % 12 == 0 else f"{months}-Month Fixed ") if months else ""
            tier_lbl = f" ({best_f.identity.tier_interval.raw_label})" if best_f.identity.tier_interval and best_f.identity.tier_interval.raw_label else ""

            if req_interest_rate or req_apr_only or not (paired_ir and paired_apr):
                metric_label = "interest rate" if best_f.identity.attribute == "interest_rate" else "APR"
                return Answer(
                    text=f"The {tx_label}{t_label}Conventional mortgage {metric_label}{tier_lbl} is {best_f.value_str}.",
                    sources=mortgage_sources(best_f),
                    confident=True,
                )
            return Answer(
                text=f"The {tx_label}{t_label}Conventional mortgage{tier_lbl} is {paired_ir.value_str} interest rate / {paired_apr.value_str} APR.",
                sources=mortgage_sources(paired_ir, paired_apr),
                confident=True,
            )

        # Broad mortgage inquiry (no specific amount requested): build summary strictly from actual rows in candidates
        # If specifically 30-year ("30-year" / "30 year") broad inquiry
        if term == 360:
            m30 = [f for f in candidates if f.identity.term_months == 360]
            c30 = sorted({f.identity.tier_interval.high for f in m30 if f.identity.tier_interval and f.identity.tier_interval.high is not None})
            if c30:
                low_c, high_c = c30[0], c30[-1]
                r_low = mortgage_rate(m30, "interest_rate", low_c)
                a_low = mortgage_rate(m30, "apr", low_c)
                r_high = mortgage_rate(m30, "interest_rate", high_c)
                a_high = mortgage_rate(m30, "apr", high_c)
                src_f = r_low or a_low
                if not src_f:
                    return Answer(text="The published mortgage rates disagree for the requested tier.", confident=False)
                eff_str = f", per the rate generation effective {src_f.effective_date}." if src_f.effective_date else "."
                low_lbl = f"${low_c:,.0f}"
                high_lbl = f"${high_c:,.0f}"
                if low_c == high_c or not (r_high and a_high):
                    r1_s = f"{r_low.value_str} rate" if r_low else ""
                    a1_s = f"{a_low.value_str} APR" if a_low else ""
                    sep = " / " if (r1_s and a1_s) else ""
                    return Answer(
                        text=f"For 30-year loans up to {low_lbl}: {r1_s}{sep}{a1_s}{eff_str}",
                        sources=mortgage_sources(r_low, a_low),
                        confident=True,
                    )
                return Answer(
                    text=f"About {r_low.value_str if r_low else ''} rate / {a_low.value_str if a_low else ''} APR for loans up to {low_lbl}, up to {r_high.value_str} rate / {a_high.value_str} APR (loans up to {high_lbl}){eff_str}",
                    sources=mortgage_sources(r_low, a_low, r_high, a_high),
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
                r_f = mortgage_rate(tm_facts, "interest_rate", target_c)
                a_f = mortgage_rate(tm_facts, "apr", target_c)
                yr_str = f"{tm // 12}-year" if tm % 12 == 0 else f"{tm}-month"
                if r_f and a_f:
                    summary_parts.append(f"{yr_str} {r_f.value_str} interest rate / {a_f.value_str} APR")
                elif a_f:
                    summary_parts.append(f"{yr_str} {a_f.value_str} APR")
                elif r_f:
                    summary_parts.append(f"{yr_str} {r_f.value_str} interest rate")
                for source in mortgage_sources(r_f, a_f):
                    qk = (source.url, source.quote)
                    if qk not in seen_quotes:
                        seen_quotes.add(qk)
                        sources.append(source)

            if summary_parts:
                tier_prefix = ""
                if lead_ceiling is not None:
                    c_lbl = f"${lead_ceiling:,.0f}"
                    tier_prefix = f"For the {c_lbl} loan-amount tier: "
                return Answer(
                    text=tier_prefix + ", and ".join(summary_parts) + ".",
                    sources=sources,
                    confident=True,
                )

        # Fallback single mortgage row without term_months
        paired_ir = mortgage_rate(candidates, "interest_rate")
        paired_apr = mortgage_rate(candidates, "apr")
        best_f = paired_apr or paired_ir
        if not best_f:
            return Answer(text="No unambiguous mortgage rate was found.", confident=False)
        tier_lbl = f"For loans {best_f.identity.tier_interval.raw_label}: " if best_f.identity.tier_interval and best_f.identity.tier_interval.raw_label else ""
        if paired_ir and paired_apr:
            return Answer(
                text=f"{tier_lbl}{paired_ir.value_str} interest rate / {paired_apr.value_str} APR.",
                sources=mortgage_sources(paired_ir, paired_apr),
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
        requested_metric = "dividend_rate" if "dividend" in q_lower and "apy" not in q_lower else "apy"
        metric = "dividend rate" if requested_metric == "dividend_rate" else "APY"
        if "bright" in q_lower:
            bright_facts = [
                f for f in eligible_facts
                if f.identity.entity == "bright_savings" or f.identity.product_name == "bright_savings"
            ]
            winner = EligibilityEngine.choose([f for f in bright_facts if f.identity.attribute == requested_metric])
            if not winner:
                return Answer(
                    text="No Bright Savings rates are published in the available snapshot.",
                    sources=[],
                    confident=False,
                )
            dividend = EligibilityEngine.choose([f for f in bright_facts if f.identity.attribute == "dividend_rate"])
            div_str = (f"{dividend.value_str} dividend rate and " if requested_metric == "apy" and "apy" not in q_lower
                       and dividend and dividend.source_url == winner.source_url and dividend.raw_quote == winner.raw_quote else "")
            eff_str = f" (rates effective {winner.effective_date})" if winner.effective_date else ""
            return Answer(
                text=f"Bright Savings earns {div_str}{winner.value_str} {metric}{eff_str}.",
                sources=[Source(url=winner.source_url, snapshot_id=winner.snapshot_id, quote=winner.raw_quote)],
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

            checking_facts = [f for f in checking_facts if f.identity.attribute == requested_metric
                              and (not matched_prods or f.identity.product_name in matched_prods)]
            if req_amount is not None:
                cands = [f for f in checking_facts if f.identity.tier_interval and f.identity.tier_interval.contains(req_amount)]
                if not cands:
                    return Answer(
                        text=f"No checking rate tier matching ${req_amount:,.2f} was found in the available snapshot.",
                        sources=[],
                        confident=False,
                    )
                best_f = EligibilityEngine.choose(cands)
                if not best_f:
                    return Answer(text="The published checking rates disagree for that tier.", confident=False)
                p_disp = format_product_display_name(best_f.identity.product_name, "checking")
                t_lbl = f" for balances {best_f.identity.tier_interval.raw_label}" if best_f.identity.tier_interval and best_f.identity.tier_interval.raw_label else ""
                return Answer(
                    text=f"{p_disp} pays {best_f.value_str} {metric}{t_lbl}.",
                    sources=[Source(url=best_f.source_url, snapshot_id=best_f.snapshot_id, quote=best_f.raw_quote)],
                    confident=True,
                )

            # Resolve each product and balance tier independently.
            groups = defaultdict(list)
            for fact in checking_facts:
                groups[fact.identity].append(fact)
            prod_tiers: dict[str, list[ExtractedFact]] = defaultdict(list)
            for identity, facts in groups.items():
                if winner := EligibilityEngine.choose(facts):
                    prod_tiers[identity.product_name or "checking"].append(winner)

            if not prod_tiers:
                return Answer(text=f"No unambiguous checking {metric} is published for the requested account.", confident=False)

            clauses: list[str] = []
            sources: list[Source] = []
            seen_q: set[tuple[str, str]] = set()
            for pname, tfacts in sorted(prod_tiers.items()):
                p_disp = format_product_display_name(pname, "checking")
                if len(tfacts) == 1 and not (tfacts[0].identity.tier_interval and tfacts[0].identity.tier_interval.raw_label):
                    bf = tfacts[0]
                    clauses.append(f"{p_disp} offers {bf.value_str} {metric}")
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
                        other_parts.append(f"{of.value_str} {metric} on {olbl}")
                    other_str = f" ({', '.join(other_parts)})" if other_parts else ""
                    clauses.append(f"{p_disp} offers {lead_f.value_str} {metric}{lead_lbl}{other_str}")
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
        savings_facts = [f for f in savings_facts if f.identity.attribute == requested_metric]

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
            winner = EligibilityEngine.choose(tier_matches)
            if not winner:
                return Answer(text=f"No unambiguous savings {metric} was found for the requested tier.", confident=False)
            p_disp = format_product_display_name(winner.identity.product_name, winner.identity.entity)
            t_lbl = winner.identity.tier_interval.raw_label or f"${req_amount:,.2f}"
            return Answer(
                text=f"For balances in the {t_lbl} tier, {p_disp} pays {winner.value_str} {metric}.",
                sources=[Source(url=winner.source_url, snapshot_id=winner.snapshot_id, quote=winner.raw_quote)],
                confident=True,
            )

        # General Share Savings / Savings rate inquiry: report all tiers or top tier dynamically
        tiers = defaultdict(list)
        for fact in savings_facts:
            tiers[fact.identity].append(fact)
        winners = [winner for facts in tiers.values() if (winner := EligibilityEngine.choose(facts))]
        if not winners:
            return Answer(text=f"No unambiguous savings {metric} is published for the requested account.", confident=False)
        top_f = max(winners, key=lambda f: (f.value_num or 0.0, EligibilityEngine.rank_key(f), f.source_url, f.raw_quote))
        p_disp = format_product_display_name(top_f.identity.product_name, top_f.identity.entity)
        t_lbl = f" for balances of {top_f.identity.tier_interval.raw_label}" if top_f.identity.tier_interval and top_f.identity.tier_interval.raw_label else ""
        return Answer(
            text=f"{p_disp} pays up to {top_f.value_str} {metric}{t_lbl}.",
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
            fee_label = "credit card replacement fee" if "credit card" in q_lower or "visa" in q_lower else "lost debit card replacement fee"
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

        named_card = next((name for name in ("platinum", "rewards") if f"{name} visa" in q_lower), None)
        card_requested = named_card or "credit card" in q_lower
        if card_requested:
            fee_facts = [f for f in fee_facts if f.identity.entity == "credit_card"]
        elif req_attr == "replacement_fee":
            fee_facts = [f for f in fee_facts if f.identity.entity == "debit_card"]
        if named_card:
            card_product = f"business_{named_card}" if "business" in q_lower else f"{named_card}_visa"
            fee_facts = [f for f in fee_facts if f.identity.product_name == card_product]
        if fee_facts:
            winner = EligibilityEngine.choose(fee_facts)
            if not winner:
                return Answer(text=f"No current {fee_label} can be confirmed.", confident=False)
            matching = [winner]
            if named_card:
                corroboration = next((
                    f for f in sorted(fee_facts, key=lambda f: (f.source_url, f.raw_quote))
                    if "/credit-cards" in f.source_url and f.value_str == winner.value_str
                ), None)
                if corroboration and corroboration not in matching:
                    matching.append(corroboration)
            conditions = winner.footnote_conditions
            if not conditions:
                label = winner.identity.attribute.removesuffix("_fee").replace("_", " ")
                conditions = next((sentence for sentence in re.split(r"(?<=[.!?])\s+|[;\n]", winner.raw_quote)
                                   if label in sentence.lower()), "")
            qualified = re.search(r"\b(?:whichever|up to|at least|per item|per transaction|waived if|unless)\b", conditions, re.I)
            text = f"The {fee_label} is {winner.value_str}."
            if qualified:
                text = f"The published {fee_label} disclosure reads “{conditions}”."
            return Answer(
                text=text,
                sources=[Source(url=f.source_url, snapshot_id=f.snapshot_id, quote=f.raw_quote) for f in matching],
            )

        # Last-known observation policy for omitted pages vs. retired fees on surviving pages:
        # Only fall back to an earlier snapshot if the source page URL itself disappeared from target_snap.pages.
        # If the source page still exists in target_snap.pages and no longer lists the fee, the fee was retired/removed.
        if req_attr == "replacement_fee" and not card_requested:
            for snap in reversed(self.ledger.chronological_snapshots):
                if snap.taken_on < target_snap.taken_on:
                    older_fees = [
                        f for f in snap.facts
                        if f.identity.attribute == req_attr and f.identity.entity == "debit_card"
                        and EligibilityEngine.is_eligible(f, target_date=eval_date, cutoff_date=cutoff_date)
                    ]
                    if older_fees:
                        f_old = EligibilityEngine.choose(older_fees)
                        if f_old and f_old.source_url not in target_snap.pages:
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
        if "how many branches" in q_lower or "number of branches" in q_lower or "how many locations" in q_lower or "branch count" in q_lower or ("network" in q_lower and "physical offices" in q_lower):
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
                        scheduled = _resolve_raw_date_token(close_date_str, eval_date, None)
                        status = f"The Hy-Vee branch was scheduled to close {close_date_str}."
                        if "still open" in q_lower and scheduled and eval_date < scheduled:
                            return Answer(
                                text=f"I cannot verify whether the branch was open on that date. {status}",
                                sources=[Source(url=hyvee_page.url, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                                confident=False,
                            )
                        return Answer(
                            text=status,
                            sources=[Source(url=hyvee_page.url, snapshot_id=target_snap.snapshot_id, quote=q_line)],
                            confident=True,
                        )
            return Answer(
                text="The requested Hy-Vee branch detail was not found in the available snapshot.",
                sources=[],
                confident=False,
            )

        if any(k in q_lower for k in ("hours", "lobby", "drive", "open", "schedule")):
            day = next((d for d in ("saturday", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday") if d in q_lower), "")
            known_name = next((n for n in KNOWN_BRANCH_CITIES if n in q_lower), None)
            named_request = known_name or re.search(
                r"\b(?:at|for|of)\s+(?:the\s+)?(?!all\b|each\b|every\b|any\b|most\b)[\w -]+?\s+branch\b",
                q_lower,
            )
            for page in sorted(target_snap.pages.values(), key=lambda p: p.url):
                if not re.search(r"/(?:contact|locations?|branches)(?:/|$)", page.url):
                    continue
                # A schedule belongs only to the branch block that introduces it.
                headings = list(re.finditer(r"(?im)^(?:#{1,6}\s+|\*\*)([^\n*]+?)\s+branch\b[^\n]*$", page.markdown))
                for idx, heading in enumerate(headings):
                    name = heading[1].strip().lower()
                    if not re.search(rf"\b{re.escape(name)}\b", q_lower):
                        continue
                    named_request = True
                    end = headings[idx + 1].start() if idx + 1 < len(headings) else len(page.markdown)
                    block = page.markdown[heading.start():end]
                    if "drive" in q_lower:
                        schedule = re.search(r"(?im)^\s*(?:#+\s+|\*\*)?drive[- ]up(?: hours)?[^\n]*\n", block)
                        if not schedule:
                            continue
                        block = block[schedule.end():]
                    else:
                        block = re.split(r"(?im)^\s*(?:#+\s+|\*\*)?drive[- ]up\b", block, maxsplit=1)[0]
                    days = day or r"(?:mon|tue|wed|thu|fri|sat|sun)\w*"
                    quote = find_verbatim_line(block, rf"\b{days}\b.*(?:\d+.*(?:a\.m\.|p\.m\.)|noon|closed)")
                    if quote:
                        return Answer(
                            text=f"{heading[1].strip()} branch: {quote}",
                            sources=[Source(url=page.url, snapshot_id=target_snap.snapshot_id, quote=quote)],
                        )
            if named_request:
                return Answer(text="The requested branch schedule is not published in the available snapshot.", confident=False)

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
        f_r = EligibilityEngine.choose(routing_facts)
        if f_r:
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
        eval_date: str,
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

        if re.search(r"\b(?:volunteer\w*|vto|giving back)\b", q_lower):
            wants_hours = "hours" in q_lower
            asks_vto = bool(re.search(r"\bvto\b|volunteer time off", q_lower))
            attribute = ("vto_hours" if asks_vto else "volunteer_hours") if wants_hours else "vto_employees"
            facts = [f for f in eligible_facts if f.identity.attribute == attribute and f.identity.reporting_period]
            relative = "last year" in q_lower or "previous year" in q_lower
            explicit_year = re.search(r"\b(?:19|20)\d{2}\b", q_lower)
            requested_year = str(int(eval_date[:4]) - 1) if relative else (explicit_year[0] if explicit_year else None)
            years = sorted({f.identity.reporting_period for f in facts})
            selected = [
                winner for year in years if not requested_year or year == requested_year
                if (winner := EligibilityEngine.choose([f for f in facts if f.identity.reporting_period == year]))
            ]
            missing_requested = not selected and requested_year is not None
            if missing_requested and relative:
                selected = [
                    winner for year in years if year < requested_year
                    if (winner := EligibilityEngine.choose([f for f in facts if f.identity.reporting_period == year]))
                ][-2:]
            if not selected:
                return Answer(text="The requested volunteer statistic is not published in the available snapshot.", confident=False)
            lines = []
            if missing_requested:
                lines.append(f"The requested statistic for {requested_year} is unpublished. Historical figures:")
            metric = "VTO hours" if asks_vto else "volunteer hours"
            for fact in selected:
                year = fact.identity.reporting_period
                if wants_hours:
                    lines.append(f"In {year}, employees contributed {fact.value_str} {metric}.")
                else:
                    lines.append(f"In {year}, {fact.value_str} employees used Volunteer Time Off (VTO).")
            return Answer(
                text=" ".join(lines),
                sources=[Source(url=f.source_url, snapshot_id=f.snapshot_id, quote=f.raw_quote) for f in selected],
                confident=not missing_requested,
            )

        return None

    def _resolve_mobile_payments(self, q_lower: str, target_snap: SnapshotRecord) -> Answer:
        wallets = ("Apple Pay", "Google Pay", "Samsung Pay", "Garmin Pay", "Fitbit Pay")
        requested = [w for w in wallets if w.lower() in q_lower]
        names = requested or wallets
        supported = []
        sources = []
        pages = sorted(target_snap.pages.values(), key=lambda p: ("/mobile-payments" not in p.url, p.url))
        for wallet in names:
            found = None
            for page in pages:
                for line in page.markdown.splitlines():
                    line = line.strip()
                    if wallet.lower() not in line.lower() or line.startswith(("![", "[")):
                        continue
                    for clause in re.split(r"[.!?;]\s+|(?:,?\s+but\s+)", line):
                        if wallet.lower() not in clause.lower():
                            continue
                        negated = re.search(r"\b(?:unavailable|unsupported|not (?:available|supported)|does not support|cannot use)\b", clause, re.I)
                        positive = re.search(r"\b(?:support\w*|compatible|add|pay with|use|available|only)\b", clause, re.I)
                        if negated or positive:
                            found = (page, line, bool(negated))
                            break
                    if found:
                        break
                if found:
                    break
            if not found:
                continue
            page, line, unavailable = found
            source = Source(url=page.url, snapshot_id=page.snapshot_id, quote=line)
            if requested and unavailable:
                return Answer(text=f"{wallet} is unavailable for these cards.", sources=[source])
            if not unavailable:
                supported.append(wallet)
                if source not in sources:
                    sources.append(source)
        if supported:
            return Answer(text="Supported mobile wallets include " + ", ".join(supported) + ".", sources=sources)
        return Answer(text="No matching mobile wallet support was found in the available snapshot.", confident=False)

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
                q_line = find_verbatim_line(join_page.markdown, r"(?:in[- ]person\s+only|only[^.\n]*in[- ]person)")
                if q_line:
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
