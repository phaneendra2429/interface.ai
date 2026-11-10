"""
Parser module for extracting structured facts, document sections, and metadata
from markdown pages across all crawled snapshots.
Adheres strictly to ARCHITECTURE_AND_DESIGN.md v5.0.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional, Any

# Safe centralized contract import guard
if "contract" not in sys.modules:
    harness_dir = next((d / "harness" for d in Path(__file__).resolve().parents if (d / "harness").is_dir()), None)
    if harness_dir is not None and str(harness_dir) not in sys.path:
        sys.path.insert(0, str(harness_dir))

try:
    from models import (
        IntervalBound,
        FactIdentity,
        ExtractedFact,
        DocumentSection,
        PageRecord,
        TypedValue,
    )
    from sanitizer import (
        clean_markdown_text,
        clean_url_path,
        extract_effective_date,
        extract_expiry_date,
        extract_footnotes,
        is_modal_url,
    )
except ImportError:
    from solution.models import (
        IntervalBound,
        FactIdentity,
        ExtractedFact,
        DocumentSection,
        PageRecord,
        TypedValue,
    )
    from solution.sanitizer import (
        clean_markdown_text,
        clean_url_path,
        extract_effective_date,
        extract_expiry_date,
        extract_footnotes,
        is_modal_url,
    )


# Regex patterns for table recognition
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")

# Number extraction
NUM_RE = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)")
PERCENT_RE = re.compile(r"(?<![a-zA-Z0-9_%])(\d{1,2}(?:\.\d+)?|100(?:\.0+)?)\s*%(?![0-9a-fA-F])")
PERCENT_RANGE_RE = re.compile(r"(?<![a-zA-Z0-9_%])(\d{1,2}(?:\.\d+)?|100(?:\.0+)?)\s*%\s*[-–—]\s*(\d{1,2}(?:\.\d+)?|100(?:\.0+)?)\s*%(?![0-9a-fA-F])", re.I)
DOLLAR_RE = re.compile(r"\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)")

# Model year pattern
MODEL_YEAR_RE = re.compile(r"(\d{4})\s*[-–—]\s*(\d{2,4})\s*Model\s*Year", re.I)
SINGLE_MODEL_YEAR_RE = re.compile(r"(\d{4})\s*Model\s*Year", re.I)

# Month term pattern (supports "6-month", "6 Month", "6mo", etc.)
MONTH_TERM_RE = re.compile(r"(\d{1,3})\s*[-–—]?\s*(?:Months?|mo|m)\b", re.I)
YEAR_TERM_RE = re.compile(r"(\d{1,2})\s*[-–—]?\s*(?:Years?|yr)\b", re.I)

# Amount tier pattern
AMOUNT_TIER_OR_LESS_RE = re.compile(
    r"(?:\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*or\s*less|up\s+to\s+\$\s*(\d+(?:,\d{3})*(?:\.\d+)?))",
    re.I,
)
AMOUNT_RANGE_RE = re.compile(r"\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*[-–—]\s*\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)", re.I)
AMOUNT_PLUS_RE = re.compile(r"\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:\+|\s*or\s*more|\s*and\s*over)", re.I)


def compute_authority_score(url: str) -> float:
    """Assign canonical authority score based on URL role."""
    if is_modal_url(url):
        return 0.0

    p = url.lower()
    if "/rates" in p or "#rates" in p:
        return 1.00
    if "/personal/credit-cards" in p or "/business/credit-cards" in p:
        return 0.95
    if "/personal/loans/auto-motorcycle-loans" in p or "/personal/loans/home-loans" in p:
        return 0.95
    if "/personal/savings-account" in p or "/personal/checking-accounts" in p:
        return 0.95
    if "/personal/loans/personal-loans" in p or "/personal/loans/home-equity" in p:
        return 0.95
    if "/contact" in p or "/about" in p or "/business/cash-management" in p:
        return 0.90
    if "/newsroom/" in p:
        return 0.70
    if "-lp" in p or "/switch-and-earn" in p:
        return 0.40
    if "/calculators/" in p:
        return 0.25
    if "/learn/blog/" in p:
        return 0.20
    return 0.50


def parse_page_sections(page_raw_md: str, url: str, snapshot_id: str) -> list[DocumentSection]:
    """Split markdown into logical sections by headings, preserving verbatim raw markdown slices."""
    sections: list[DocumentSection] = []
    lines = page_raw_md.splitlines(keepends=True)
    
    current_heading = "Overview"
    current_raw_lines: list[str] = []

    for line in lines:
        if line.lstrip().startswith(("# ", "## ", "### ", "#### ")):
            if current_raw_lines:
                raw_slice = "".join(current_raw_lines)
                clean_slice = clean_markdown_text(raw_slice)
                sections.append(DocumentSection(
                    url=url,
                    snapshot_id=snapshot_id,
                    heading=current_heading,
                    raw_markdown=raw_slice,
                    clean_markdown=clean_slice,
                ))
                current_raw_lines = []
            current_heading = line.strip().lstrip("#").strip()
        current_raw_lines.append(line)

    if current_raw_lines:
        raw_slice = "".join(current_raw_lines)
        clean_slice = clean_markdown_text(raw_slice)
        sections.append(DocumentSection(
            url=url,
            snapshot_id=snapshot_id,
            heading=current_heading,
            raw_markdown=raw_slice,
            clean_markdown=clean_slice,
        ))

    return sections


def parse_markdown_tables(page_raw_md: str, url: str, snapshot_id: str, taken_on: str, authority: float) -> list[ExtractedFact]:
    """Extract structured facts from markdown tables."""
    facts: list[ExtractedFact] = []
    raw_lines = page_raw_md.splitlines()

    i = 0
    n = len(raw_lines)

    page_eff_date = extract_effective_date(page_raw_md)
    page_exp_date = extract_expiry_date(page_raw_md)
    page_footnotes = extract_footnotes(page_raw_md)

    while i < n:
        line = raw_lines[i]
        if "|" in line and TABLE_ROW_RE.match(line):
            # Check if next line is table separator
            if i + 1 < n and TABLE_SEP_RE.match(raw_lines[i + 1]):
                headers = [c.strip() for c in line.split("|")[1:-1]]
                table_rows_raw: list[tuple[str, list[str]]] = []
                j = i + 2
                while j < n and "|" in raw_lines[j] and not TABLE_SEP_RE.match(raw_lines[j]):
                    row_line = raw_lines[j]
                    cols = [c.strip() for c in row_line.split("|")[1:-1]]
                    if cols and any(c for c in cols):
                        table_rows_raw.append((row_line, cols))
                    j += 1
                
                # Immediate heading lines (walk backward from i-1, stopping at links, footnotes, or previous tables)
                imm_heading_lines: list[str] = []
                for k in range(i - 1, max(-1, i - 7), -1):
                    s_line = raw_lines[k].strip()
                    if not s_line:
                        continue
                    s_clean = s_line.lstrip("#*").strip()
                    if (
                        "](" in s_line
                        or "|" in s_line
                        or (s_clean and s_clean[0].isdigit())
                        or s_line.startswith("_")
                        or len(s_clean) > 80
                        or "rates are effective" in s_clean.lower()
                        or "annual percentage" in s_clean.lower()
                    ):
                        break
                    imm_heading_lines.insert(0, s_clean)
                imm_heading = " ".join(imm_heading_lines).lower()

                # Context window for dates and footnotes bounded to the current table's block
                post_lines: list[str] = []
                for k in range(j, min(n, j + 16)):
                    s_line = raw_lines[k].strip()
                    if "|" in s_line or s_line.startswith("#") or s_line.startswith("[Open") or s_line.startswith("[Apply") or s_line.startswith("[See "):
                        break
                    post_lines.append(raw_lines[k])
                post_table_text = "\n".join(post_lines)
                pre_table_text = "\n".join(imm_heading_lines)
                bounded_context = pre_table_text + "\n" + post_table_text

                table_eff_date = (
                    extract_effective_date(post_table_text)
                    or extract_effective_date(pre_table_text)
                    or page_eff_date
                )
                table_exp_date = (
                    extract_expiry_date(post_table_text)
                    or extract_expiry_date(pre_table_text)
                    or page_exp_date
                )
                table_footnotes = extract_footnotes(post_table_text) or page_footnotes

                # Determine table domain / entity with precision from immediate heading and headers
                entity_hint = ""
                account_category = "personal"
                headers_str = " ".join(headers).lower()

                if re.search(r"\b(?:atv|snowmobile|jet ski|boat|rv|recreational)\b", imm_heading):
                    entity_hint = "recreational_loan"
                elif "auto" in imm_heading or "motorcycle" in imm_heading or "auto" in headers_str:
                    entity_hint = "auto_loan"
                elif "heloc" in imm_heading or "home equity line" in imm_heading or "heloc" in headers_str:
                    entity_hint = "heloc"
                elif "home equity" in imm_heading:
                    entity_hint = "home_equity_loan"
                elif "home improvement" in imm_heading:
                    entity_hint = "home_improvement_loan"
                elif "home loan" in imm_heading or "mortgage" in imm_heading or "homebuying" in imm_heading:
                    entity_hint = "mortgage"
                elif "certificate" in imm_heading or "certificate" in headers_str or "cd" in headers_str:
                    entity_hint = "certificate"
                elif "checking" in imm_heading or "checking" in headers_str:
                    entity_hint = "checking"
                elif "money market" in imm_heading or "money market" in headers_str:
                    entity_hint = "money_market"
                elif "credit card" in imm_heading or "visa" in imm_heading:
                    entity_hint = "credit_card"
                elif "personal loan" in imm_heading:
                    entity_hint = "personal_loan"
                elif "fee" in imm_heading or "fee" in headers_str:
                    entity_hint = "fee"
                elif "savings" in imm_heading or "share savings" in imm_heading or "bright savings" in imm_heading or "savings" in headers_str:
                    entity_hint = "savings"
                else:
                    # Fallback to URL hints
                    u_l = url.lower()
                    if "auto" in u_l:
                        entity_hint = "auto_loan"
                    elif "home-loan" in u_l or "homebuying" in u_l:
                        entity_hint = "mortgage"
                    elif "home-equity" in u_l:
                        entity_hint = "heloc"
                    elif "savings" in u_l:
                        entity_hint = "savings"
                    elif "checking" in u_l:
                        entity_hint = "checking"
                    elif "credit-card" in u_l:
                        entity_hint = "credit_card"
                    elif "personal-loan" in u_l:
                        entity_hint = "personal_loan"
                    elif "fee-schedule" in u_l:
                        entity_hint = "fee"

                if "hsa" in imm_heading or "health savings" in imm_heading or "health savings" in headers_str:
                    account_category = "health_savings"
                elif "ira" in imm_heading or "retirement" in imm_heading or "ira" in headers_str:
                    account_category = "ira"
                elif "business" in imm_heading or "business" in url.lower() or "business" in headers_str:
                    account_category = "business"

                # Parse the table rows
                table_facts = _parse_table_rows(
                    headers=headers,
                    rows=table_rows_raw,
                    entity_hint=entity_hint,
                    account_category=account_category,
                    imm_heading=imm_heading,
                    url=url,
                    snapshot_id=snapshot_id,
                    taken_on=taken_on,
                    authority=authority,
                    effective_date=table_eff_date,
                    expiry_date=table_exp_date,
                    context_window=bounded_context,
                    footnotes=table_footnotes,
                )
                facts.extend(table_facts)
                i = j
                continue
        i += 1

    return facts


def _classify_fee_label(label_l: str) -> tuple[str, str, str]:
    """Return (entity, product_name, attribute) for a fee description label."""
    card = "platinum" if "platinum" in label_l else "rewards" if "rewards" in label_l else ""
    product = (f"business_{card}" if "business" in label_l else f"{card}_visa") if card else ""
    if "foreign transaction" in label_l:
        return ("credit_card", product, "foreign_transaction_fee")
    if "cash advance" in label_l:
        return ("credit_card", product, "cash_advance_fee")
    if "balance transfer" in label_l:
        return ("credit_card", product, "balance_transfer_fee")
    if "annual fee" in label_l:
        return ("credit_card", product, "annual_fee")
    if "late payment" in label_l or "late fee" in label_l:
        return ("credit_card" if ("card" in label_l or "visa" in label_l) else "fee", "late_fee", "late_fee")
    if "overdraft" in label_l or "courtesy pay" in label_l or "paid items" in label_l or "nsf" in label_l:
        return ("overdraft", "overdraft", "overdraft_fee")
    if "replacement" in label_l:
        if product or "credit card" in label_l:
            return ("credit_card", product, "replacement_fee")
        return ("debit_card", "debit_card", "replacement_fee")
    if "statement" in label_l:
        return ("paper_statement", "paper_statement", "monthly_fee")
    norm_prod = re.sub(r"[^a-z0-9]+", "_", label_l).strip("_")[:40]
    return ("fee", norm_prod, "fee_amount")


def _parse_table_rows(
    headers: list[str],
    rows: list[tuple[str, list[str]]],
    entity_hint: str,
    account_category: str,
    imm_heading: str,
    url: str,
    snapshot_id: str,
    taken_on: str,
    authority: float,
    effective_date: Optional[str],
    expiry_date: Optional[str],
    context_window: str,
    footnotes: dict[str, str],
) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    has_ira_column = any(re.search(r"\bira\b", h, re.I) for h in headers)
    
    col_types: list[str] = []
    for h in headers:
        hl = h.lower()
        if "apr" in hl or "annual percentage rate" in hl:
            col_types.append("apr")
        elif "apy" in hl or "annual percentage yield" in hl:
            col_types.append("apy")
        elif "dividend rate" in hl:
            col_types.append("dividend_rate")
        elif "interest rate" in hl or hl == "rate":
            col_types.append("interest_rate")
        elif "term" in hl:
            col_types.append("term")
        elif re.search(r"\bmin(?:imum)?\b", hl):
            col_types.append("minimum")
        elif "balance" in hl or "program" in hl or "certificate" in hl or "loan program" in hl or "card" in hl:
            col_types.append("product_or_tier")
        elif "fee" in hl and ("amount" in hl or hl == "fee" or hl == "fees"):
            col_types.append("fee_val")
        else:
            col_types.append("other")

    # Pre-scan table rows for amount ceilings ("$X or less" / "Up to $X") partitioned by (term_months, transaction_type)
    # Fixes descending mortgage ceiling inversion and avoids cross-program ceiling mixing
    ceilings_by_group: dict[tuple[Optional[int], str], set[float]] = {}
    for _, cols in rows:
        row_text_raw = clean_markdown_text(" ".join(cols))
        row_text_raw_l = row_text_raw.lower()
        m_ceil = AMOUNT_TIER_OR_LESS_RE.search(row_text_raw)
        if m_ceil:
            c_str = m_ceil.group(1) or m_ceil.group(2)
            c_val = float(c_str.replace(",", ""))
            r_tx = ""
            if "purchase" in row_text_raw_l or "purchase" in imm_heading:
                r_tx = "purchase"
            elif "refinance" in row_text_raw_l or "refinance" in imm_heading:
                r_tx = "refinance"
            r_term: Optional[int] = None
            m_m = MONTH_TERM_RE.search(row_text_raw)
            if m_m:
                r_term = int(m_m.group(1))
            else:
                m_y = YEAR_TERM_RE.search(row_text_raw)
                if m_y:
                    r_term = int(m_y.group(1)) * 12
            ceilings_by_group.setdefault((r_term, r_tx), set()).add(c_val)

    sorted_ceilings_by_group = {k: sorted(list(v)) for k, v in ceilings_by_group.items()}

    prev_row_entity: Optional[str] = None
    prev_row_product: str = ""

    table_category = account_category
    for raw_line, cols in rows:
        account_category = table_category
        row_text = " ".join(cols)
        row_text_clean = clean_markdown_text(row_text)
        row_text_l = row_text_clean.lower()
        
        # Product qualifiers
        is_jumbo = "jumbo" in row_text_l or "jumbo" in imm_heading
        is_special = "special" in row_text_l or "special" in imm_heading
        is_intro = "intro" in row_text_l or "introductory" in row_text_l
        
        transaction_type = ""
        if "purchase" in row_text_l or "purchase" in imm_heading:
            transaction_type = "purchase"
        elif "refinance" in row_text_l or "refinance" in imm_heading:
            transaction_type = "refinance"

        # Term extraction
        term_months: Optional[int] = None
        m_month = MONTH_TERM_RE.search(row_text_clean)
        if m_month:
            term_months = int(m_month.group(1))
        else:
            m_year = YEAR_TERM_RE.search(row_text_clean)
            if m_year:
                term_months = int(m_year.group(1)) * 12

        # Tier interval extraction
        tier_interval: Optional[IntervalBound] = None
        m_model = MODEL_YEAR_RE.search(row_text_clean)
        if m_model:
            y1 = int(m_model.group(1))
            raw_y2 = m_model.group(2)
            y2 = int(raw_y2) if len(raw_y2) == 4 else int(str(y1)[:2] + raw_y2)
            tier_interval = IntervalBound(low=float(y1), high=float(y2), unit="year", raw_label=f"{y1}-{y2}")
        else:
            m_single_my = SINGLE_MODEL_YEAR_RE.search(row_text_clean)
            if m_single_my:
                y = float(m_single_my.group(1))
                if "older" in row_text_l:
                    tier_interval = IntervalBound(low=None, high=y, unit="year", raw_label=f"<={int(y)}")
                else:
                    tier_interval = IntervalBound(low=y, high=y, unit="year", raw_label=f"{int(y)}")
            else:
                m_or_less = AMOUNT_TIER_OR_LESS_RE.search(row_text_clean)
                group_ceilings = sorted_ceilings_by_group.get((term_months, transaction_type), [])
                if m_or_less and group_ceilings:
                    c_str = m_or_less.group(1) or m_or_less.group(2)
                    ceil_val = float(c_str.replace(",", ""))
                    idx = group_ceilings.index(ceil_val)
                    low_b = 0.0 if idx == 0 else group_ceilings[idx - 1]
                    tier_interval = IntervalBound(
                        low=low_b,
                        high=ceil_val,
                        low_inclusive=False,
                        high_inclusive=True,
                        unit="$",
                        raw_label=f"<=${ceil_val:,.0f}",
                    )
                else:
                    m_range = AMOUNT_RANGE_RE.search(row_text_clean)
                    if m_range:
                        low_v = float(m_range.group(1).replace(",", ""))
                        high_v = float(m_range.group(2).replace(",", ""))
                        tier_interval = IntervalBound(
                            low=low_v,
                            high=high_v,
                            low_inclusive=True,
                            high_inclusive=True,
                            unit="$",
                            raw_label=f"${low_v:,.0f}-${high_v:,.2f}",
                        )
                    else:
                        m_plus = AMOUNT_PLUS_RE.search(row_text_clean)
                        if m_plus:
                            low_v = float(m_plus.group(1).replace(",", ""))
                            tier_interval = IntervalBound(
                                low=low_v,
                                high=None,
                                low_inclusive=True,
                                high_inclusive=True,
                                unit="$",
                                raw_label=f"${low_v:,.0f}+",
                            )

        # Entity and product_name identification with priority
        entity = entity_hint
        product_name = ""

        if entity_hint == "recreational_loan":
            product_name = "recreational_loan"
        elif "home-equity" in url.lower() and re.match(r"loan\s*[-–—]", row_text_l):
            entity = product_name = "home_equity_loan"
        elif "heloc" in row_text_l or "home equity line" in row_text_l or "heloc" in imm_heading:
            entity = "heloc"
            product_name = "heloc"
        elif "home equity loan" in row_text_l or "home equity loan" in imm_heading or entity == "home_equity_loan":
            entity = "home_equity_loan"
            product_name = "home_equity_loan"
        elif "home improvement" in row_text_l or "home improvement" in imm_heading or entity == "home_improvement_loan":
            entity = "home_improvement_loan"
            product_name = "home_improvement_loan"
        elif "hsa" in row_text_l or "health savings" in row_text_l or "health savings" in imm_heading:
            entity = "health_savings"
            product_name = "health_savings"
            account_category = "health_savings"
        elif ("certificate" in row_text_l or "certificate" in imm_heading or entity == "certificate"
              or any("certificate" in h.lower() for h in headers)
              or (has_ira_column and term_months is not None)):
            entity = "certificate"
            product_name = "certificate_special" if is_special else "certificate_regular"
            ira_subtype = re.search(r"\b(traditional|roth)(?=\s*:|\s+ira\b)", row_text_l + " " + imm_heading)
            if ira_subtype:
                account_category = f"{ira_subtype[1]}_ira"
        elif "1st rate checking" in row_text_l or "1st rate checking" in imm_heading or "1st rate" in row_text_l:
            entity = "checking"
            product_name = "business_1st_rate_checking" if account_category == "business" else "1st_rate_checking"
        elif "free checking" in row_text_l or "free checking" in imm_heading:
            entity = "checking"
            product_name = "business_free_checking" if account_category == "business" else "free_checking"
        elif "analysis checking" in row_text_l or "analysis checking" in imm_heading:
            entity = "checking"
            product_name = "business_analysis_checking"
        elif "bright savings" in row_text_l or "bright savings" in imm_heading:
            entity = "bright_savings"
            product_name = "bright_savings"
        elif "high yield savings" in row_text_l or "high yield savings" in imm_heading:
            entity = "savings"
            product_name = "high_yield_savings"
        elif "money market" in row_text_l or "money market" in imm_heading or entity == "money_market":
            entity = "money_market"
            product_name = "investors_choice_money_market" if "investor" in row_text_l or "investor" in imm_heading else "money_market"
        elif "share savings" in row_text_l or "share savings" in imm_heading:
            entity = "share_savings"
            product_name = "share_savings"
        elif "launchpad" in row_text_l or "launchpad" in imm_heading:
            entity = "savings"
            product_name = "launchpad_savings"
        elif "holiday club" in row_text_l or "holiday club" in imm_heading:
            entity = "savings"
            product_name = "holiday_club_savings"
        elif "you name it" in row_text_l or "you name it" in imm_heading:
            entity = "savings"
            product_name = "you_name_it_savings"
        elif "ira" in row_text_l or "ira" in imm_heading or account_category == "ira":
            if entity != "certificate":
                entity = "savings"
                product_name = "ira_savings"
        elif "credit card" in row_text_l or "visa" in row_text_l or "card type" in " ".join(headers).lower() or "credit cards" in imm_heading:
            entity = "credit_card"
            h_str = " ".join(headers).lower()
            if "business" in url.lower() or "business" in imm_heading or "business" in h_str:
                account_category = "business"
            if account_category == "business":
                if "platinum" in row_text_l or "platinum" in imm_heading or "platinum" in h_str:
                    product_name = "business_platinum"
                elif "rewards" in row_text_l or "rewards" in imm_heading or "rewards" in h_str:
                    product_name = "business_rewards"
            else:
                if "rewards" in row_text_l or "rewards" in imm_heading or "rewards" in h_str:
                    product_name = "rewards_visa"
                elif "platinum" in row_text_l or "platinum" in imm_heading or "platinum" in h_str:
                    product_name = "platinum_visa"
        elif "personal loan" in row_text_l or "personal loans" in imm_heading:
            entity = "personal_loan"
            product_name = "personal_loan"
        elif "model year" in row_text_l or "auto" in imm_heading or "motorcycle" in imm_heading:
            entity = "auto_loan"
            product_name = "auto_loan"
        elif "conventional" in row_text_l or "home loans" in imm_heading or "mortgage" in imm_heading:
            entity = "mortgage"
            product_name = "mortgage"

        # Dynamic product_name fallback for novel checking/savings/card products (e.g. "Prime Business Checking")
        if not product_name and entity in ("checking", "savings", "money_market", "credit_card"):
            if "checking-offer-lp" in url.lower():
                product_name = "1st_rate_checking"
            else:
                first_col = clean_markdown_text(cols[0]).strip() if cols else ""
                if (
                    first_col
                    and not first_col.startswith("$")
                    and not first_col[0].isdigit()
                    and "balance" not in first_col.lower()
                    and len(first_col) <= 40
                ):
                    product_name = re.sub(r"[^a-z0-9]+", "_", first_col.lower()).strip("_")
                elif imm_heading and len(imm_heading) <= 40 and any(k in imm_heading for k in ("checking", "savings", "money market", "visa", "card")):
                    product_name = re.sub(r"[^a-z0-9]+", "_", imm_heading).strip("_")

        if not product_name and prev_row_product and cols and clean_markdown_text(cols[0]).strip().startswith("$"):
            entity = prev_row_entity or entity
            product_name = prev_row_product

        if not entity:
            entity = "general"

        if entity != "general" and product_name:
            prev_row_entity = entity
            prev_row_product = product_name

        # Check for attached footnote markers
        footnote_text = None
        for fn_k, fn_v in footnotes.items():
            if re.search(rf"(?:%|APR|APY)\s*{re.escape(fn_k)}\s*(?:$|\*)", row_text, re.I) or any(
                re.search(rf"(?:%|APR|APY)\s*{re.escape(fn_k)}\s*(?:$|\*)", cell, re.I) for cell in cols
            ):
                footnote_text = fn_v
                break

        # Handle fee schedule table rows (e.g. | Consumer overdrafts (paid items) | $30.00 per item |)
        if (entity == "fee" or "fee_val" in col_types) and len(cols) >= 2:
            fee_desc = clean_markdown_text(cols[0]).strip()
            fee_val_raw = clean_markdown_text(cols[1]).strip()
            f_ent, f_prod, f_attr = _classify_fee_label(f"{imm_heading} {fee_desc}".lower())
            m_f_dlr = DOLLAR_RE.search(fee_val_raw)
            m_f_pct = PERCENT_RE.search(fee_val_raw)
            if m_f_dlr:
                d_num = float(m_f_dlr.group(1).replace(",", ""))
                d_str = f"${d_num:.2f}"
                facts.append(ExtractedFact(
                    identity=FactIdentity(
                        entity=f_ent,
                        product_name=f_prod,
                        attribute=f_attr,
                        account_category=account_category,
                    ),
                    value_str=d_str,
                    value_num=d_num,
                    unit="$",
                    source_url=url,
                    snapshot_id=snapshot_id,
                    taken_on=taken_on,
                    authority_score=authority,
                    effective_date=effective_date,
                    expiry_date=expiry_date,
                    footnote_text=footnote_text,
                    footnote_conditions=fee_val_raw,
                    typed_value=TypedValue(is_range=False, low=d_num, high=None, unit="$", raw_str=d_str),
                    raw_quote=raw_line.strip(),
                ))
            elif m_f_pct:
                p_num = float(m_f_pct.group(1))
                p_str = f"{m_f_pct.group(1)}%"
                facts.append(ExtractedFact(
                    identity=FactIdentity(
                        entity=f_ent,
                        product_name=f_prod,
                        attribute=f_attr,
                        account_category=account_category,
                    ),
                    value_str=p_str,
                    value_num=p_num,
                    unit="%",
                    source_url=url,
                    snapshot_id=snapshot_id,
                    taken_on=taken_on,
                    authority_score=authority,
                    effective_date=effective_date,
                    expiry_date=expiry_date,
                    footnote_text=footnote_text,
                    footnote_conditions=fee_val_raw,
                    typed_value=TypedValue(is_range=False, low=p_num, high=None, unit="%", raw_str=p_str),
                    raw_quote=raw_line.strip(),
                ))
            elif re.search(r"\b(?:free|no\s+fee|waived|none)\b", fee_val_raw, re.I):
                facts.append(ExtractedFact(
                    identity=FactIdentity(
                        entity=f_ent,
                        product_name=f_prod,
                        attribute=f_attr,
                        account_category=account_category,
                    ),
                    value_str="$0.00",
                    value_num=0.0,
                    unit="$",
                    source_url=url,
                    snapshot_id=snapshot_id,
                    taken_on=taken_on,
                    authority_score=authority,
                    effective_date=effective_date,
                    expiry_date=expiry_date,
                    footnote_text=footnote_text,
                    footnote_conditions=fee_val_raw,
                    typed_value=TypedValue(is_range=False, low=0.0, high=None, unit="$", raw_str="$0.00"),
                    raw_quote=raw_line.strip(),
                ))

        # Extract values for columns
        seen_rate_ctypes_in_row: set[str] = set()
        for c_idx, val in enumerate(cols):
            if c_idx >= len(col_types):
                continue
            ctype = col_types[c_idx]
            if ctype == "minimum" and entity == "certificate":
                cleaned_min = clean_markdown_text(val).strip()
                m_min_dlr = DOLLAR_RE.search(cleaned_min)
                if m_min_dlr:
                    min_num = float(m_min_dlr.group(1).replace(",", ""))
                    min_str = f"${min_num:,.0f}" if min_num.is_integer() else f"${min_num:,.2f}"
                    facts.append(ExtractedFact(
                        identity=FactIdentity(
                            entity=entity,
                            product_name=product_name,
                            attribute="minimum_balance",
                            account_category=account_category,
                            is_jumbo=is_jumbo,
                            is_special=is_special,
                            is_intro=is_intro,
                            transaction_type=transaction_type,
                            term_months=term_months,
                            tier_interval=tier_interval,
                        ),
                        value_str=min_str,
                        value_num=min_num,
                        unit="$",
                        source_url=url,
                        snapshot_id=snapshot_id,
                        taken_on=taken_on,
                        authority_score=authority,
                        effective_date=effective_date,
                        expiry_date=expiry_date,
                        footnote_text=footnote_text,
                        typed_value=TypedValue(is_range=False, low=min_num, high=None, unit="$", raw_str=min_str),
                        raw_quote=raw_line.strip(),
                    ))
            elif ctype in ("apr", "apy", "dividend_rate", "interest_rate"):
                cleaned_val = clean_markdown_text(val).strip()

                # Check for range (e.g. 12.50% - 19.50% or 15.50% - 21.00%)
                m_range = PERCENT_RANGE_RE.search(cleaned_val)
                if m_range:
                    r_low = float(m_range.group(1))
                    r_high = float(m_range.group(2))
                    val_str = f"{r_low:.2f}% - {r_high:.2f}%"
                    num_val = r_low
                    typed_val = TypedValue(is_range=True, low=r_low, high=r_high, unit="%", raw_str=val_str)
                else:
                    m_pct = PERCENT_RE.search(cleaned_val)
                    if m_pct:
                        num_val = float(m_pct.group(1))
                        val_str = f"{m_pct.group(1)}%"
                        typed_val = TypedValue(is_range=False, low=num_val, high=None, unit="%", raw_str=val_str)
                    else:
                        continue

                col_is_intro = is_intro
                if ctype in seen_rate_ctypes_in_row:
                    col_is_intro = not is_intro
                seen_rate_ctypes_in_row.add(ctype)

                ident = FactIdentity(
                    entity=entity,
                    product_name=product_name,
                    attribute=ctype,
                    account_category=account_category,
                    is_jumbo=is_jumbo,
                    is_special=is_special,
                    is_intro=col_is_intro,
                    transaction_type=transaction_type,
                    term_months=term_months,
                    tier_interval=tier_interval,
                )

                facts.append(ExtractedFact(
                    identity=ident,
                    value_str=val_str,
                    value_num=num_val,
                    unit="%",
                    source_url=url,
                    snapshot_id=snapshot_id,
                    taken_on=taken_on,
                    authority_score=authority,
                    effective_date=effective_date,
                    expiry_date=expiry_date,
                    footnote_text=footnote_text,
                    typed_value=typed_val,
                    raw_quote=raw_line.strip(),
                ))

    return facts


def parse_colon_facts(page_raw_md: str, url: str, snapshot_id: str, taken_on: str, authority: float) -> list[ExtractedFact]:
    """Parse colon-delimited and bullet rate/fee lines (e.g. from credit card pages, selftest, or spec sheets)."""
    if "/learn/blog/" in url.lower():
        return []
    facts: list[ExtractedFact] = []
    lines = page_raw_md.splitlines()

    page_eff_date = extract_effective_date(page_raw_md)
    page_exp_date = extract_expiry_date(page_raw_md)
    page_footnotes = extract_footnotes(page_raw_md)

    current_section_heading = ""

    for line in lines:
        line_s = line.strip()
        if line_s.startswith(("# ", "## ", "### ", "#### ")):
            current_section_heading = line_s.lstrip("#").strip().lower()
            continue

        # Strip markdown link targets before matching numbers so %20 in URLs never matches PERCENT_RE
        line_no_urls = re.sub(r"\]\([^)]*\)", "]", line_s)
        line_clean = clean_markdown_text(line_no_urls).strip()
        if "|" in line_s or not line_clean:
            continue

        line_l = line_clean.lower()
        # Require either an explicit colon spec ("Label: Value") with a short label, or an explicit bullet starting with APR/APY/Rate/Fee
        if ":" in line_clean:
            lhs = line_clean.split(":", 1)[0].strip()
            if len(lhs) > 55 or len(line_clean) > 150:
                continue
        else:
            if not re.match(r"^[-*•]?\s*(?:apr|apy|rate|interest\s+rate|dividend\s+rate|fee|replacement|overdraft)\b", line_l):
                continue
            if len(line_clean) > 140:
                continue

        # Skip illustrative payment examples, credit utilization advice, split ratios, and ARM cap disclosures
        if any(
            kw in line_l
            for kw in (
                "for example",
                "payment example",
                "monthly payment obligation",
                "if you borrow",
                "down payment",
                "deductible",
                "purchase price",
                "rate range based on creditworthiness",
                "available credit",
                "credit line",
                "credit score",
                "adjust up to",
                "original rate",
                "spent up to",
                "split ratio",
                "split intentionally",
                "toward a goal",
            )
        ):
            continue

        # Check for card / bullet rate lines
        # e.g. "- APR1: 15.50%–21.00%" or "- APR2: 12.50%–19.50%" or "Rate: 5.39%" or "Fee: $2.50"
        m_range = PERCENT_RANGE_RE.search(line_clean)
        m_pct = PERCENT_RE.search(line_clean)
        m_dlr = DOLLAR_RE.search(line_clean)
        m_free_fee = re.search(r"fee[^:]*:\s*(?:free|no\s+fee|waived|none)\b", line_clean, re.I)

        if not (m_range or m_pct or m_dlr or m_free_fee):
            continue

        full_label = f"{current_section_heading} {line_clean}".lower()
        line_exp_date = extract_expiry_date(line_clean) or page_exp_date

        entity = "general"
        product_name = ""
        attribute = "rate"
        term_months: Optional[int] = None
        is_special = "special" in full_label
        is_intro = "intro" in full_label or "new cardmember" in full_label
        account_category = "personal"
        if "business" in full_label or "business" in url.lower():
            account_category = "business"

        is_fee_line = any(
            k in full_label
            for k in (
                "fee",
                "overdraft",
                "courtesy pay",
                "replacement",
                "foreign transaction",
                "cash advance",
                "paper statement",
            )
        )

        if is_fee_line:
            entity, product_name, attribute = _classify_fee_label(full_label)
        elif "business credit card" in full_label or "/business/credit-cards" in url.lower():
            account_category = "business"
            entity = "credit_card"
            if "platinum" in full_label:
                product_name = "business_platinum"
            elif "rewards" in full_label:
                product_name = "business_rewards"
            elif current_section_heading:
                product_name = re.sub(r"[^a-z0-9]+", "_", current_section_heading).strip("_")
        elif "credit card" in full_label or "rewards visa" in full_label or "platinum visa" in full_label or "visa" in full_label or "/personal/credit-cards" in url.lower():
            entity = "credit_card"
            if "rewards" in full_label:
                product_name = "rewards_visa"
            elif "platinum" in full_label:
                product_name = "platinum_visa"
            elif current_section_heading:
                product_name = re.sub(r"[^a-z0-9]+", "_", current_section_heading).strip("_")
        elif "certificate" in full_label or "cd" in full_label:
            entity = "certificate"
            attribute = "apy"
        elif re.search(r"\b(?:auto|cars?|vehicle|motorcycle)\b", full_label) or "/auto" in url.lower():
            entity = "auto_loan"
            attribute = "apr"
        elif "mortgage" in full_label or "home loan" in full_label or "/home-loan" in url.lower():
            entity = "mortgage"
            attribute = "apr" if "apr" in full_label else "interest_rate"
        elif "heloc" in full_label or "home equity" in full_label:
            entity = "heloc"
            attribute = "apr"

        m_month = MONTH_TERM_RE.search(full_label)
        if m_month:
            term_months = int(m_month.group(1))
        else:
            m_year = YEAR_TERM_RE.search(full_label)
            if m_year:
                term_months = int(m_year.group(1)) * 12

        tier_interval: Optional[IntervalBound] = None
        m_model = MODEL_YEAR_RE.search(full_label)
        if m_model:
            y1 = int(m_model.group(1))
            raw_y2 = m_model.group(2)
            y2 = int(raw_y2) if len(raw_y2) == 4 else int(str(y1)[:2] + raw_y2)
            tier_interval = IntervalBound(low=float(y1), high=float(y2), unit="year", raw_label=f"{y1}-{y2}")

        # Attached footnote
        footnote_text = None
        for fn_k, fn_v in page_footnotes.items():
            if fn_k in line_clean:
                footnote_text = fn_v
                break
        if not footnote_text and "platinum" in product_name:
            # Check for Platinum Visa introductory footnote
            for fn_v in page_footnotes.values():
                if "platinum visa introductory" in fn_v.lower() or "first 90 days" in fn_v.lower():
                    footnote_text = fn_v
                    break

        if m_range and not is_intro:
            r_low = float(m_range.group(1))
            r_high = float(m_range.group(2))
            val_str = f"{r_low:.2f}% - {r_high:.2f}%"
            num_val = r_low
            typed_val = TypedValue(is_range=True, low=r_low, high=r_high, unit="%", raw_str=val_str)
            unit = "%"
            attr = attribute if is_fee_line else ("apr" if "apr" in full_label else attribute)

            facts.append(ExtractedFact(
                identity=FactIdentity(
                    entity=entity,
                    product_name=product_name,
                    attribute=attr,
                    account_category=account_category,
                    is_special=is_special,
                    is_intro=False,
                    term_months=term_months,
                    tier_interval=tier_interval,
                ),
                value_str=val_str,
                value_num=num_val,
                unit=unit,
                source_url=url,
                snapshot_id=snapshot_id,
                taken_on=taken_on,
                authority_score=authority,
                effective_date=page_eff_date,
                expiry_date=line_exp_date,
                footnote_text=footnote_text,
                typed_value=typed_val,
                raw_quote=line_s,
            ))
        elif m_pct:
            num_val = float(m_pct.group(1))
            val_str = f"{m_pct.group(1)}%"
            typed_val = TypedValue(is_range=False, low=num_val, high=None, unit="%", raw_str=val_str)
            unit = "%"
            attr = attribute if is_fee_line else ("apr" if "apr" in full_label else attribute)

            facts.append(ExtractedFact(
                identity=FactIdentity(
                    entity=entity,
                    product_name=product_name,
                    attribute=attr,
                    account_category=account_category,
                    is_special=is_special,
                    is_intro=is_intro,
                    term_months=term_months,
                    tier_interval=tier_interval,
                ),
                value_str=val_str,
                value_num=num_val,
                unit=unit,
                source_url=url,
                snapshot_id=snapshot_id,
                taken_on=taken_on,
                authority_score=authority,
                effective_date=page_eff_date,
                expiry_date=line_exp_date,
                footnote_text=footnote_text,
                typed_value=typed_val,
                raw_quote=line_s,
            ))
        elif m_dlr and is_fee_line:
            d_str = m_dlr.group(1).replace(",", "")
            num_val = float(d_str)
            val_str = f"${num_val:.2f}"
            typed_val = TypedValue(is_range=False, low=num_val, high=None, unit="$", raw_str=val_str)
            unit = "$"

            facts.append(ExtractedFact(
                identity=FactIdentity(
                    entity=entity,
                    product_name=product_name,
                    attribute=attribute,
                    account_category=account_category,
                    is_special=is_special,
                    is_intro=is_intro,
                    term_months=term_months,
                ),
                value_str=val_str,
                value_num=num_val,
                unit=unit,
                source_url=url,
                snapshot_id=snapshot_id,
                taken_on=taken_on,
                authority_score=authority,
                effective_date=page_eff_date,
                expiry_date=line_exp_date,
                footnote_text=footnote_text,
                typed_value=typed_val,
                raw_quote=line_s,
            ))
        elif m_free_fee and is_fee_line:
            facts.append(ExtractedFact(
                identity=FactIdentity(
                    entity=entity,
                    product_name=product_name,
                    attribute=attribute,
                    account_category=account_category,
                    is_special=is_special,
                    is_intro=is_intro,
                    term_months=term_months,
                ),
                value_str="$0.00",
                value_num=0.0,
                unit="$",
                source_url=url,
                snapshot_id=snapshot_id,
                taken_on=taken_on,
                authority_score=authority,
                effective_date=page_eff_date,
                expiry_date=line_exp_date,
                footnote_text=footnote_text,
                typed_value=TypedValue(is_range=False, low=0.0, high=None, unit="$", raw_str="$0.00"),
                raw_quote=line_s,
            ))

    return facts


def parse_prose_facts(page_raw_md: str, url: str, snapshot_id: str, taken_on: str, authority: float) -> list[ExtractedFact]:
    """Extract key historical statistics, routing numbers, fine-print fees, and prose facts."""
    facts: list[ExtractedFact] = []
    lines = page_raw_md.splitlines()

    vto_emp_re = re.compile(r"(\d{2,4})\s+(?:Dupaco\s+)?employees\s*\*{0,2}\s+used\b", re.I)
    vto_hours_re = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d+)\s+(?:(?:volunteer|VTO)\s+)?hours\b", re.I)
    vto_boards_re = re.compile(r"(\d{2,4})\s+(?:community\s+)?boards\b", re.I)
    year_re = re.compile(r"\b((?:19|20)\d{2})\b")
    branch_count_re = re.compile(r"\b(\d{1,3})\s+(?:branches|branch locations)\b", re.I)
    routing_re = re.compile(
        r"\b(?:aba\s+(?:routing\s+)?number|routing\s+(?:(?:and|&)\s+transit\s+)?number)"
        r"\s*[:=]?\s*(?:is\s+)?\(?(\d{9})\b", re.I,
    )

    foreign_tx_re = re.compile(r"foreign\s+transaction\s+fee\s*[—–\-:]+\s*(\d+(?:\.\d+)?)\s*%", re.I)
    cash_adv_re = re.compile(r"cash\s+advance\s+fee\s*[—–\-:]+\s*\$\s*(\d+(?:\.\d+)?)(?:\s+or\s+(\d+(?:\.\d+)?)\s*%)?", re.I)
    late_fee_re = re.compile(r"late\s+payment\s+fee\s+is\s+(?:up\s+to\s+)?\$\s*(\d+(?:\.\d+)?)", re.I)
    overdraft_prose_re = re.compile(r"overdraft\s+(?:privilege\s+)?(?:service\s+)?fee\s+is\s+\$\s*(\d+(?:\.\d+)?)", re.I)

    account_category = "business" if "business" in url.lower() else "personal"

    last_volunteer_year = None
    for line in lines:
        line_s = line.strip()
        if not line_s:
            continue
        line_l = line_s.lower()

        retired_certificate = (
            re.search(r"\b(?:certificates?|cds?)\b[^.]*\b(?:has been|have been|is|are|was|were)\s+"
                      r"(?:discontinued|retired|withdrawn|no longer offered)\b", line_l)
            and MONTH_TERM_RE.search(line_s)
        )
        if retired_certificate:
            ira = re.search(r"\b(traditional|roth)\b", line_l)
            category = f"{ira[1]}_ira" if ira else ("ira" if re.search(r"\bira\b", line_l) else account_category)
            facts.append(ExtractedFact(
                identity=FactIdentity(entity="certificate", term_months=int(retired_certificate[1]),
                                      account_category=category, is_jumbo="jumbo" in line_l,
                                      is_special="special" in line_l),
                value_str="unpublished", value_num=None, unit="", source_url=url,
                snapshot_id=snapshot_id, taken_on=taken_on, authority_score=authority,
                effective_date=extract_effective_date(line_s), raw_quote=line_s,
                supersedes=True, withdrawn=True,
            ))

        if re.search(r"(?:lost\s+)?debit card replacement", line_l):
            free = re.search(r"\b(?:free|no (?:charge|fee)|waived)\b", line_l)
            retired = re.search(r"\b(?:retired|discontinued|removed|eliminated)\b", line_l)
            if free or retired:
                facts.append(ExtractedFact(
                    identity=FactIdentity(entity="debit_card", product_name="debit_card", attribute="replacement_fee"),
                    value_str="free" if free else "unpublished",
                    value_num=0.0 if free else None,
                    unit="$" if free else "",
                    source_url=url,
                    snapshot_id=snapshot_id,
                    taken_on=taken_on,
                    authority_score=authority,
                    effective_date=extract_effective_date(line_s),
                    typed_value=TypedValue(is_range=False, low=0.0, high=None, unit="$", raw_str="free") if free else None,
                    raw_quote=line_s,
                    supersedes=bool(retired or re.search(r"\bnow\b", line_l)),
                    withdrawn=not bool(free),
                ))

        # Fine-print fee disclosures (e.g., on credit card and rate pages)
        m_ftx = foreign_tx_re.search(line_s)
        if m_ftx:
            p_val = float(m_ftx.group(1))
            p_str = f"{m_ftx.group(1)}%"
            facts.append(ExtractedFact(
                identity=FactIdentity(
                    entity="credit_card",
                    product_name=(
                        ("business_platinum" if account_category == "business" else "platinum_visa")
                        if "platinum visa" in line_l else
                        ("business_rewards" if account_category == "business" else "rewards_visa")
                        if "rewards visa" in line_l else ""
                    ),
                    attribute="foreign_transaction_fee",
                    account_category=account_category,
                ),
                value_str=p_str,
                value_num=p_val,
                unit="%",
                source_url=url,
                snapshot_id=snapshot_id,
                taken_on=taken_on,
                authority_score=authority,
                typed_value=TypedValue(is_range=False, low=p_val, high=None, unit="%", raw_str=p_str),
                raw_quote=line_s,
            ))

        m_ca = cash_adv_re.search(line_s)
        if m_ca:
            d_val = float(m_ca.group(1))
            p_part = f" or {m_ca.group(2)}%" if m_ca.group(2) else ""
            ca_str = f"${d_val:.2f}{p_part}"
            facts.append(ExtractedFact(
                identity=FactIdentity(
                    entity="credit_card",
                    attribute="cash_advance_fee",
                    account_category=account_category,
                ),
                value_str=ca_str,
                value_num=d_val,
                unit="$",
                source_url=url,
                snapshot_id=snapshot_id,
                taken_on=taken_on,
                authority_score=authority,
                typed_value=TypedValue(is_range=False, low=d_val, high=None, unit="$", raw_str=ca_str),
                raw_quote=line_s,
            ))

        m_lf = late_fee_re.search(line_s)
        if m_lf:
            l_val = float(m_lf.group(1))
            l_str = f"${l_val:.2f}"
            facts.append(ExtractedFact(
                identity=FactIdentity(
                    entity="credit_card",
                    product_name="late_fee",
                    attribute="late_fee",
                    account_category=account_category,
                ),
                value_str=l_str,
                value_num=l_val,
                unit="$",
                source_url=url,
                snapshot_id=snapshot_id,
                taken_on=taken_on,
                authority_score=authority,
                typed_value=TypedValue(is_range=False, low=l_val, high=None, unit="$", raw_str=l_str),
                raw_quote=line_s,
            ))

        m_od = overdraft_prose_re.search(line_s)
        if m_od:
            o_val = float(m_od.group(1))
            o_str = f"${o_val:.2f}"
            facts.append(ExtractedFact(
                identity=FactIdentity(
                    entity="overdraft",
                    product_name="overdraft",
                    attribute="overdraft_fee",
                    account_category=account_category,
                ),
                value_str=o_str,
                value_num=o_val,
                unit="$",
                source_url=url,
                snapshot_id=snapshot_id,
                taken_on=taken_on,
                authority_score=authority,
                typed_value=TypedValue(is_range=False, low=o_val, high=None, unit="$", raw_str=o_str),
                raw_quote=line_s,
            ))

        # Routing number (require explicit routing/ABA context and reject tracking/order numbers)
        has_non_routing_kw = "tracking" in line_l or "order" in line_l or "invoice" in line_l
        m_rt = routing_re.search(clean_markdown_text(line_s)) if not has_non_routing_kw and "number" in line_l else None
        if m_rt and not has_non_routing_kw:
            r_num = m_rt.group(1)
            rt_auth = max(authority, 0.96) if "/business/cash-management" in url.lower() else authority
            facts.append(ExtractedFact(
                identity=FactIdentity(
                    entity="credit_union",
                    attribute="routing_number",
                ),
                value_str=r_num,
                value_num=float(r_num),
                unit="",
                source_url=url,
                snapshot_id=snapshot_id,
                taken_on=taken_on,
                authority_score=rt_auth,
                typed_value=TypedValue(is_range=False, low=float(r_num), high=None, unit="", raw_str=r_num),
                raw_quote=line_s,
            ))

        # VTO facts
        if re.search(r"\b(?:volunteer\w*|vto|giving back)\b", line_l):
            years = year_re.findall(line_s)
            reporting_year = years[0] if years else last_volunteer_year
            if years:
                last_volunteer_year = years[0]
            
            m_emp = vto_emp_re.search(line_s)
            if m_emp and reporting_year and re.search(r"volunteer time off|\bvto\b", line_l):
                emp_count = int(m_emp.group(1))
                facts.append(ExtractedFact(
                    identity=FactIdentity(
                        entity="dupaco",
                        attribute="vto_employees",
                        reporting_period=reporting_year,
                    ),
                    value_str=str(emp_count),
                    value_num=float(emp_count),
                    unit="",
                    source_url=url,
                    snapshot_id=snapshot_id,
                    taken_on=taken_on,
                    authority_score=authority,
                    typed_value=TypedValue(is_range=False, low=float(emp_count), high=None, unit="", raw_str=str(emp_count)),
                    raw_quote=line_s,
                ))

            m_hrs = vto_hours_re.search(line_s)
            if m_hrs and reporting_year and not re.search(r"(?:over|nearly|about|roughly)\s+$", line_s[:m_hrs.start()], re.I):
                hours_attribute = "vto_hours" if "vto hours" in line_l else "volunteer_hours"
                hrs_str = m_hrs.group(1).replace(",", "")
                hrs_count = int(hrs_str)
                facts.append(ExtractedFact(
                    identity=FactIdentity(
                        entity="dupaco",
                        attribute=hours_attribute,
                        reporting_period=reporting_year,
                    ),
                    value_str=f"{hrs_count:,}",
                    value_num=float(hrs_count),
                    unit="",
                    source_url=url,
                    snapshot_id=snapshot_id,
                    taken_on=taken_on,
                    authority_score=authority,
                    typed_value=TypedValue(is_range=False, low=float(hrs_count), high=None, unit="", raw_str=str(hrs_count)),
                    raw_quote=line_s,
                ))

            m_bds = vto_boards_re.search(line_s)
            if m_bds and reporting_year:
                bds_count = int(m_bds.group(1))
                facts.append(ExtractedFact(
                    identity=FactIdentity(
                        entity="dupaco",
                        attribute="vto_boards",
                        reporting_period=reporting_year,
                    ),
                    value_str=str(bds_count),
                    value_num=float(bds_count),
                    unit="",
                    source_url=url,
                    snapshot_id=snapshot_id,
                    taken_on=taken_on,
                    authority_score=authority,
                    typed_value=TypedValue(is_range=False, low=float(bds_count), high=None, unit="", raw_str=str(bds_count)),
                    raw_quote=line_s,
                ))

        # Branch count
        m_br_off = re.search(r"\b(\d{1,3})\s+(?:branch\s+office\s+locations|network of branches)\b", line_s, re.I)
        m_br = branch_count_re.search(line_s)
        if m_br_off:
            b_count = int(m_br_off.group(1))
            br_auth = 0.96 if ("community development financial institution" in line_l or "cdfi" in line_l) else 0.95
            facts.append(ExtractedFact(
                identity=FactIdentity(
                    entity="dupaco",
                    attribute="branch_count",
                ),
                value_str=str(b_count),
                value_num=float(b_count),
                unit="",
                source_url=url,
                snapshot_id=snapshot_id,
                taken_on=taken_on,
                authority_score=br_auth,
                typed_value=TypedValue(is_range=False, low=float(b_count), high=None, unit="", raw_str=str(b_count)),
                raw_quote=line_s,
            ))
        elif m_br and ("dupaco" in line_l or "credit union" in line_l or "/about" in url):
            b_count = int(m_br.group(1))
            historical = re.search(r"\bin\s+(?:19|20)\d{2}\b", line_l)
            br_auth = min(authority, 0.60) if historical else authority
            facts.append(ExtractedFact(
                identity=FactIdentity(
                    entity="dupaco",
                    attribute="branch_count",
                ),
                value_str=str(b_count),
                value_num=float(b_count),
                unit="",
                source_url=url,
                snapshot_id=snapshot_id,
                taken_on=taken_on,
                authority_score=br_auth,
                typed_value=TypedValue(is_range=False, low=float(b_count), high=None, unit="", raw_str=str(b_count)),
                raw_quote=line_s,
            ))

    return facts


def parse_page(page: Any, snapshot_id: str, taken_on: str) -> tuple[PageRecord, list[ExtractedFact], list[DocumentSection]]:
    """Parse a single Page object, returning PageRecord, ExtractedFacts, and DocumentSections."""
    url = page.url
    raw_md = page.markdown or ""
    clean_path = clean_url_path(url)
    authority = compute_authority_score(url)
    clean_md = clean_markdown_text(raw_md)

    title = getattr(page, "title", "") or ""
    fetched_at = getattr(page, "fetched_at", "") or ""

    page_rec = PageRecord(
        url=url,
        clean_path=clean_path,
        title=title,
        markdown=raw_md,
        fetched_at=fetched_at,
        snapshot_id=snapshot_id,
        taken_on=taken_on,
        authority_score=authority,
    )

    sections = parse_page_sections(raw_md, url, snapshot_id)
    table_facts = parse_markdown_tables(raw_md, url, snapshot_id, taken_on, authority)
    colon_facts = parse_colon_facts(raw_md, url, snapshot_id, taken_on, authority)
    prose_facts = parse_prose_facts(raw_md, url, snapshot_id, taken_on, authority)

    all_facts = table_facts + colon_facts + prose_facts
    return page_rec, all_facts, sections
