"""Frozen scoring oracles. Imported by the submission gate, never by the solution."""

ROOT = "https://www.dupaco.com"
R = ROOT + "/rates"
C = ROOT + "/personal/savings-account"
H = ROOT + "/personal/loans/home-loans"
A = ROOT + "/personal/loans/auto-motorcycle-loans"
CC = ROOT + "/personal/credit-cards"
B = ROOT + "/business-rates"
IRA = ROOT + "/personal/retirement"

# id, question, as_of, required tokens (None means refusal), forbidden tokens
LEGACY = [
    ("T1", "What is the APR for a 60-month auto loan on a 2025 vehicle?", "2026-05-12", ["5.19%"], ["5.39%"]),
    ("T2", "What was the 10-month jumbo certificate APY?", "2026-08-27", ["3.90%"], ["4.00%"]),
    ("T3", "What was the 10-month jumbo certificate APY as of May 31, 2026?", None, ["3.90%"], ["4.00%"]),
    ("T4", "What was the 17-month jumbo certificate APY?", "2026-06-09", ["3.95%"], ["4.20%", "4.10%"]),
    ("T5", "What was the 30-year fixed purchase mortgage APR on $100,000?", "2026-05-19", ["6.388%"], ["7.27%", "7.29%"]),
    ("T6", "Compare the APR for a 60-month auto loan on a 2025 vehicle on May 12 versus September 28, 2026.", None, ["5.19%", "5.39%"], []),
    ("T7", "What is the 10-month jumbo certificate APY?", "2026-05-11", None, []),
    ("T8", "What was the 60-month auto loan APR for a 2025 vehicle on September 28, 2026?", "2026-05-31", None, []),
    ("Q1", "What is the APR for a 60-month auto loan for a 2023 model-year vehicle?", None, ["5.64%"], ["5.39%"]),
    ("Q2", "What is the APR for a 72-month auto loan on a 2025 model-year vehicle?", None, ["5.49%"], []),
    ("Q3", "What is the 30-year fixed conventional purchase mortgage APR on $150,000?", None, ["7.34%"], ["7.27%", "7.29%"]),
    ("Q4", "What is the 30-year fixed conventional refinance mortgage APR on $150,000?", None, ["7.35%"], ["7.27%", "7.29%", "7.34%"]),
    ("Q5", "What is the 15-year fixed purchase mortgage APR?", None, ["6.88%"], ["7.27%", "7.29%"]),
    ("Q6", "What APY does Share Savings pay on $9,999.99?", None, ["0.10%"], ["0.15%"]),
    ("Q7", "What APY does Share Savings pay on $10,000?", None, ["0.15%"], ["0.10%"]),
    ("Q8", "What APY does Share Savings pay on $25,000?", None, ["0.20%"], ["0.15%"]),
    ("Q9", "What is the dividend rate on Prime Business Checking with a $5,000 balance?", None, ["2.47%"], ["2.50%"]),
    ("Q10", "What is the APY of the 10-month regular certificate special?", None, ["3.90%"], ["4.00%"]),
    ("Q11", "What is the dividend rate on the 17-month jumbo certificate special?", None, ["4.15%"], ["4.20%", "4.10%"]),
    ("Q12", "What is the APR on a 48-month personal loan?", None, ["10.99%"], ["10.49%"]),
    ("Q13", "How long does the introductory balance transfer APR last on Platinum Visa?", None, ["6 months", "90 days"], []),
    ("R1", "What is the lobby schedule for the imaginary Neptune Avenue branch?", None, None, []),
    ("R2", "What is the rate on a 47-month 1-week jumbo certificate?", None, None, []),
    ("R3", "What is the rate on a seven-over-one adjustable-rate mortgage?", None, None, []),
    ("E1", "What is the standard foreign transaction fee on the personal Platinum Visa credit card?", None, ["1.00%"], []),
    ("E2", "What is the amount of the consumer overdraft fee per item?", None, ["$30.00"], []),
    ("E3", "How many Dupaco employees used Volunteer Time Off in 2024?", None, ["256"], ["294"]),
    ("E4", "What is Dupaco's ABA number for ACH transfers?", None, ["273974581"], []),
]

PAIRS = [
    ("What is Dupaco's routing number?", "What is Dupaco's ABA number for ACH transfers?", ["273974581"]),
    ("What is the APR for a 60-month auto loan on a 2025 vehicle?", "What annual percentage rate applies to five years of financing for a 2025 car?", ["5.39%"]),
    ("What is the APR for an 84-month auto loan on a 2024 vehicle?", "What is the APR for seven years of auto financing on a 2024 vehicle?", ["5.59%"]),
    ("How many branches does Dupaco have?", "What is the size of Dupaco's network of physical offices?", ["22"]),
    ("What was the 10-month jumbo certificate APY as of May 31, 2026?", "What was the APY on a May 2026 ten-month jumbo share certificate?", ["3.90%"]),
    ("What is your rate on a 7/1 adjustable-rate mortgage?", "What is the rate on a seven-over-one adjustable-rate mortgage?", None),
    ("What is the address of the Galena Branch?", "Where can I find your Galena office?", ["11375 Oldenburg", "61036"]),
    ("What is the APY on the 17-month special certificate?", "What is the APY on a 17 month special CD?", ["4.10%"]),
]

EXTRA = [
    ("X01", "What APR do I get on a 48-month auto loan for a 2010 model?", None, ["8.59%"], ["5.19%"], [R, A]),
    ("X02", "What is the APR on a 30-month auto loan for a 2025 vehicle?", None, None, [], []),
    ("X03", "What APR applies to a 60-month car loan on a 2099 model-year vehicle?", None, None, [], []),
    ("X04", "What was the 60-month auto loan APR for a 2025 model-year vehicle as of May 12, 2026?", "2026-09-28", ["5.19%"], ["5.39%"], [R, A]),
    ("X05", "What is the refinance 30-year fixed mortgage APR on $150k?", None, ["7.35%"], ["7.28%", "7.29%"], [R, H, ROOT + "/personal/loans/home-equity"]),
    ("X06", "What is the 30-year purchase mortgage APR on $100,000.01?", None, ["7.34%"], ["7.27%"], [R, H]),
    ("X07", "What is the 30-year purchase mortgage APR on $200,000.01?", None, ["7.32%"], ["7.34%"], [R, H]),
    ("X08", "What is the 30-year purchase mortgage APR on $832,750?", None, ["7.29%"], ["7.27%"], [R, H]),
    ("X09", "What is the 30-year purchase mortgage APR on $832,750.01?", None, None, [], []),
    ("X10", "What is the standard fixed home-equity loan APR for 60 months?", None, ["6.74%"], ["6.25%", "7.24%", "7.29%"], [R, ROOT + "/personal/loans/home-equity"]),
    ("X11", "What APY does the 10-month jumbo Traditional IRA certificate pay?", None, ["3.90%"], ["4.00%"], [R, IRA]),
    ("X12", "What APY does the 10-month jumbo Roth IRA certificate pay?", None, ["4.00%"], ["3.90%"], [R, IRA]),
    ("X13", "What is the dividend rate on the six-month regular certificate?", None, ["3.37%"], ["3.40%"], [R, C]),
    ("X14", "What is the APY on a 15-month standard share certificate?", None, None, [], []),
    ("X15", "What APY does personal Share Savings pay at $99.99?", None, ["0.00%"], ["0.10%"], [R, C]),
    ("X16", "What APY does personal Share Savings pay at $100.00?", None, ["0.10%"], ["0.00%", "0.20%"], [R, C]),
    ("X17", "What is the APY on a Health Savings Account with $25,000?", None, ["0.35%"], ["0.20%", "0.30%"], [R, C]),
    ("X18", "What is the Investor's Choice Money Market APY on $100,000?", None, ["0.40%"], ["0.35%"], [R, C]),
    ("X19", "What is the APY on Business 1st Rate Checking at $4,999.99?", None, ["0.00%"], ["2.50%"], [B, ROOT + "/business/checking-accounts", ROOT + "/business/business-checking-accounts"]),
    ("X20", "What is the personal Platinum Visa foreign transaction fee?", None, ["1.00%"], ["$30.00", "3.00%"], [CC]),
    ("X21", "What is the standard APR range of the personal Rewards Visa?", None, ["15.50%", "21.00%"], ["12.50%", "19.50%", "2.90%"], [CC, R]),
    ("X22", "How many hours did Dupaco employees volunteer in 2024?", None, ["6,187"], ["7,260"], [ROOT + "/newsroom/volunteering-pays-off-dupaco-credit-union-employees-reap-the-benefits-of-giving-back-2"]),
    ("X23", "How many Dupaco employees used Volunteer Time Off in 2025?", None, None, [], []),
    ("X24", "What is the APR on a 24-month personal loan?", None, ["9.99%"], ["10.49%"], [R, ROOT + "/personal/loans/personal-loans"]),
]


def table(heading, columns, rows):
    return "\n".join([
        f"## {heading}", "", "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
    ])


def cd(value, note="", path="/rates/certificates", term=6):
    return path, f"{term}-month certificate APY: {value}{note}"


def cds(rows, path="/rates/certificates", heading="Certificate Rates", minimum="Minimum Balance"):
    return path, table(heading, ["Certificate", "Dividend Rate", "APY", minimum], rows)


def cards(rows):
    return "/personal/credit-cards", table("Credit Cards", ["Card", "APR"], rows)


def auto(rows, path="/rates/auto", note=""):
    return path, table("Auto Loan Rates", ["Loan Program", "Term", "APR"], [
        (f"{year} Model Year", f"{term} Months", f"{value}%") for year, term, value in rows
    ]) + "\n\n" + note


def mortgages(rows):
    return "/rates", table("Mortgage Rates", ["Program", "Rate", "APR"], rows)


def gold(topic, values, resolution=None):
    return {"id": topic, "topic_keywords": [topic], "values": values, "correct_value": resolution}


def audit_conflicts():
    return [
        ("authority", [[auto([("2029-31", 60, "4.37")]), auto([("2029-31", 60, "5.61")], "/calculators/auto")]], [gold("auto", ["4.37%", "5.61%"], "4.37%")]),
        ("replacement", [[cd("3.17%")], [cd("4.79%", path="/certificates")]], [gold("certificate", ["3.17%", "4.79%"], "4.79%")]),
        ("zero", [[cd("0.00%")], [cd("3.17%")]], [gold("certificate", ["0.00%", "3.17%"], "3.17%")]),
        ("format", [[cd("3.50%")], [cd("3.5%")]], []),
        ("metric", [[cds([("6 Month", "3.40%", "3.47%", "$500")])]], []),
        ("range", [[cards([("Platinum Visa", "8.25% - 12.25%")])], [cards([("Platinum Visa", "8.25% - 14.25%")])]], [gold("platinum visa", ["8.25% - 12.25%", "8.25% - 14.25%"], "8.25% - 14.25%")]),
        ("product", [[cards([("Platinum Visa", "8.25% - 12.25%"), ("Rewards Visa", "10.75% - 14.50%")])]], []),
        ("reband", [[auto([("2027-30", 60, "4.21")])], [auto([("2025-27", 60, "5.14"), ("2028-31", 60, "3.95")])]], [
            gold("auto", ["4.21%", "5.14%"], "5.14%"), gold("auto", ["4.21%", "3.95%"], "3.95%")]),
        ("expired", [[cd("3.17%", " (offer ended 2031-02-11)")], [cd("4.79%", " (offer ended 2031-03-02)")]], [gold("certificate", ["3.17%", "4.79%"])]),
        ("future", [[cd("3.17%"), cd("4.79%", "\nRates are effective as of 04/01/2031.", "/certificate-offer")]], [gold("certificate", ["3.17%", "4.79%"], "3.17%")]),
        ("removed-all", [[cd("3.17%")], [cd("4.79%")], [("/news", "The certificate was discontinued.")]], [gold("certificate", ["3.17%", "4.79%"])]),
        ("distinct-ira", [[cds([("Roth: 10 Month Fixed Jumbo Special", "3.85%", "3.91%", "$50,000"), ("Traditional: 10 Month Fixed Jumbo Special", "4.01%", "4.07%", "$60,000")], "/personal/retirement", "IRA Certificate Rates", "Min. to Earn")]], []),
        ("mortgage-tiers", [[mortgages([("Purchase: 30-Year Fixed Conventional $220,000 or less", "4.10%", "4.25%"), ("Refinance: 30-Year Fixed Conventional $400,000 or less", "4.70%", "4.89%"), ("Purchase: 30-Year Fixed Conventional $80,000 or less", "3.78%", "3.93%")])]], []),
    ]


def mutations():
    worlds = {
        "world": [[cd("2.91%")], [cd("8.25%")]],
        "expiry": [[cd("7.77%", " (offer ended 2031-02-12)")]],
        "future": [[cd("7.77%", "\nRates are effective as of 04/01/2031.")]],
        "branches": [[("/contact", "### Pine Avenue branch\nCall us for details.\n\n### Cedar Falls branch\nSaturday: 11 a.m. to 1 p.m.\nAddress: 16 Walnut Street.")]],
        "noise": [[("/contact", "For assistance, use the contact form."), ("/join", "Applications are now only handled in person."), ("/about", "Forbes was mentioned in a member survey. No awards were issued."), ("/routing", "Order tracking number: 273912345."), ("/branch-news", "Hy-Vee branch is scheduled to close July 2, 2032."), ("/mobile-payments", "Apple Pay only. Google Pay and Samsung Pay are unavailable."), ("/personal/savings-account", "Savings calculator. No CD rates published.")]],
        "card": [[cards([("Platinum Visa", "8.25% - 12.25%"), ("Rewards Visa", "10.75% - 14.50%")])]],
        "mort": [[mortgages([("Purchase: 30-Year Fixed Conventional $550,000 or less", "4.10%", "4.25%")])]],
        "fees": [[("/fees", "Lost debit card replacement fee: $7.99")], [("/notice", "Lost debit card replacement is now free. The old fee has been retired.")]],
        "prods": [[cds([("19 Month", "3.79%", "3.85%", "$725"), ("19 Month Jumbo Special", "4.21%", "4.29%", "$83,000")])]],
        "auto": [[auto([("2027-30", 60, "4.21"), ("2027-30", 48, "3.79")])]],
    }
    cases = [
        ("M01", "world", "What was the 6-month certificate APY as of February 10, 2031?", "2031-04-10", ["2.91%"], ["8.25%"]),
        ("M02", "world", "What was the 6-month certificate APY on 02/10/2031?", None, ["2.91%"], ["8.25%"]),
        ("M03", "world", "What was the 6-month certificate APY in February 2031?", None, ["2.91%"], ["8.25%"]),
        ("M04", "world", "Compare the six-month certificate APY on February 10 versus April 10, 2031.", None, ["2.91%", "8.25%"], []),
        ("M05", "world", "What will the 6-month certificate APY be on December 10, 2031?", None, None, []),
        ("M06", "expiry", "What is the 6-month certificate APY?", "2031-02-13", None, []),
        ("M07", "expiry", "What is the 6-month certificate APY?", "2031-02-12", ["7.77%"], []),
        ("M08", "future", "What is the 6-month certificate APY?", None, None, []),
        ("M09", "branches", "What are the Pine Avenue branch Saturday lobby hours?", None, None, []),
        ("M10", "noise", "What are the lobby hours?", None, None, []),
        ("M11", "noise", "Can I join online?", None, ["in person"], ["join.dupaco.com"]),
        ("M12", "noise", "What recognition did Forbes give Dupaco?", None, None, []),
        ("M13", "noise", "What is the ABA number?", None, None, []),
        ("M14", "noise", "When is the Hy-Vee branch scheduled to close?", None, ["July 2, 2032"], ["March 31, 2026"]),
        ("M15", "noise", "Can I add my Dupaco card to Google Pay?", None, ["unavailable"], []),
        ("M16", "noise", "Where can I find the link to certificate rates?", None, None, []),
        ("M17", "card", "What is the standard purchase APR on Platinum Visa?", None, ["8.25%", "12.25%"], ["19.50%", "10.75%"]),
        ("M18", "card", "How many credit cards do you offer and what are their APRs?", None, ["two", "8.25%", "10.75%"], ["four"]),
        ("M19", "mort", "What is the 30-year refinance mortgage APR on $100,000?", None, None, []),
        ("M20", "fees", "What is the lost debit card replacement fee?", None, ["free"], ["$7.99"]),
        ("M21", "fees", "What is the Platinum Visa annual fee?", None, None, []),
        ("M22", "prods", "What APY and minimum balance does the 19-month regular certificate require?", None, ["3.85%", "$725"], ["4.29%", "$83,000"]),
        ("M23", "prods", "What is the dividend rate of the 19-month jumbo certificate?", None, ["4.21%"], ["4.29%", "3.85%"]),
        ("M24", "auto", "What is the APR for a 60-month auto loan on a 2028 model-year vehicle?", None, ["4.21%"], ["3.79%"]),
    ]
    return worlds, cases


def challenge():
    """Twenty-four independent intents, each expressed twice."""
    worlds = {
        "calendar": [[cd("2.83%", term=11)], [cd("3.71%", term=11)]],
        "expiry": [[cd("8.12%", " (offer ended 2034-03-12)", term=11)]],
        "tiers": [[cds([("23 Month", "4.13%", "4.21%", "$900"), ("23 Month Fixed Jumbo Special", "4.45%", "4.55%", "$72,000")])]],
        "mortgage": [[mortgages([("Refinance: 30-Year Fixed Conventional $340,000 or less", "6.11%", "6.43%"), ("Refinance: 30-Year Fixed Conventional $190,000 or less", "5.90%", "6.24%")])]],
        "car": [[auto([("2030-32", 48, "6.43"), ("2033-36", 48, "5.66"), ("2033-36", 60, "6.08")])]],
        "ira": [[cds([("Traditional: 14 Month Fixed Jumbo Special", "4.21%", "4.33%", "$40,000"), ("Roth: 14 Month Fixed Jumbo Special", "4.46%", "4.55%", "$75,000")], "/personal/retirement", "IRA Certificate Rates")]],
        "savings": [[("/business-rates", table("Business 1st Rate Checking", ["Account Type", "Balance Tier", "Dividend Rate", "APY"], [("Business 1st Rate Checking", "$5,000+", "2.36%", "2.42%")]))]],
        "fees": [[("/fees", "Lost debit card replacement fee: $11.25")], [("/fee-update", "Lost debit card replacement is now free. The former replacement fee was retired."), ("/fees", "Paper statement fee: $3.25")]],
        "wallet": [[("/mobile-payments", "Apple Pay is supported. Google Pay and Samsung Pay are unavailable.")]],
        "card": [[("/personal/credit-cards", "## Platinum Visa\n\nThe 4.10% introductory APR for balance transfers lasts 8 months. Transfers must post within 45 days.\nThere is no annual fee.")]],
        "stats": [[("/newsroom/community", "In 2033, 478 employees used paid Volunteer Time Off.\nIn 2033, employees volunteered 8,218 hours.\nIn 2032, employees volunteered 7,151 hours.")]],
        "branches": [[("/contact", "### Pine Avenue branch\nLobby hours:\nSaturday: 10 a.m. to noon.\n\n### Cedar Falls branch\nLobby hours:\nSaturday: 11 a.m. to 1 p.m.")]],
        "ties": [[cd("3.33%", path="/rates/a", term=11), cd("4.27%", path="/rates/b", term=11)]],
    }
    cases = [
        ("calendar", None, ["2.83%"], ["3.71%"], "What was the 11-month certificate APY on 03/10/2034?", "What was the eleven-month CD APY on March 10, 2034?"),
        ("calendar", None, ["2.83%", "3.71%"], [], "Compare the 11-month CD APY on March 10 versus June 10, 2034.", "How did the eleven-month certificate APY change from 2034-03-10 to 2034-06-10?"),
        ("calendar", "2034-03-11", None, [], "What was the 11-month CD APY on June 10, 2034?", "Give me the eleven-month certificate APY as of 2034-06-10."),
        ("expiry", "2034-03-13", None, [], "What is the 11-month certificate APY?", "What yield does the eleven-month CD pay?"),
        ("tiers", None, ["4.21%", "$900"], ["4.55%"], "What APY and minimum does the 23-month regular certificate have?", "What are the APY and minimum for a twenty-three-month regular CD?"),
        ("tiers", None, ["4.45%"], ["4.55%"], "What is the 23-month jumbo CD dividend rate?", "What dividend rate does a twenty-three-month jumbo certificate pay?"),
        ("mortgage", None, ["6.43%"], ["6.24%"], "What is the 30-year refinance mortgage APR on $190,000.01?", "What APR is listed for a thirty-year refinance mortgage at $190,000.01?"),
        ("car", None, ["5.66%"], ["6.43%", "6.08%"], "What is the 48-month auto loan APR for a 2033 model-year vehicle?", "What is the APR on a four-year car loan for a 2033 vehicle?"),
        ("ira", None, ["4.33%"], ["4.55%"], "What APY is published for the Traditional 14-month jumbo IRA certificate?", "What is the APY of the fourteen-month Traditional IRA jumbo CD?"),
        ("ira", None, ["4.46%"], ["4.55%", "4.21%"], "What is the dividend rate on a 14-month jumbo Roth IRA certificate?", "What dividend rate does the fourteen-month Roth IRA jumbo CD pay?"),
        ("savings", None, ["2.36%"], ["2.42%"], "What is the Business 1st Rate Checking dividend rate on $8,000?", "What dividend rate applies to $8k in first-rate business checking?"),
        ("tiers", None, ["4.13%"], ["4.21%"], "What is the 23-month regular CD dividend rate?", "What dividend rate does the twenty-three-month regular certificate pay?"),
        ("fees", None, ["free"], ["$11.25"], "How much is the lost debit card replacement fee?", "What is the fee to replace a lost debit card?"),
        ("fees", None, ["$3.25"], ["$11.25"], "What is the paper statement fee?", "What fee applies to a paper statement?"),
        ("wallet", None, ["unavailable"], [], "Can I use Google Pay with my card?", "Does my card support the Samsung Pay mobile wallet?"),
        ("card", None, ["8 months", "45 days"], [], "How long does the introductory balance-transfer APR on Platinum Visa last?", "What is the duration of the Platinum Visa intro balance transfer APR and the transfer window?"),
        ("stats", None, ["478"], ["8,218"], "How many employees used VTO in 2033?", "What was the Volunteer Time Off headcount in 2033?"),
        ("stats", None, ["8,218"], ["478", "7,151"], "How many hours did employees volunteer in 2033?", "What were the total employee volunteer hours in 2033?"),
        ("branches", None, ["10 a.m.", "noon"], ["11 a.m."], "What are the Pine Avenue branch Saturday lobby hours?", "On Saturday, when are the lobby hours at Pine Avenue branch?"),
        ("branches", None, ["11 a.m.", "1 p.m."], ["10 a.m."], "What are the Cedar Falls branch Saturday lobby hours?", "On Saturday, when are the lobby hours at Cedar Falls branch?"),
        ("car", None, None, [], "What is the 72-month auto loan APR for a 2033 model-year vehicle?", "Is an APR published for six years of car financing on a 2033 vehicle?"),
        ("branches", None, None, [], "What are Saturday lobby hours for the Birch Avenue branch?", "When is the Birch Avenue branch lobby open on Saturday?"),
        ("stats", None, None, [], "How many employees used VTO in 2032?", "What was the Volunteer Time Off employee headcount in 2032?"),
        ("ties", None, None, [], "What is the 11-month certificate APY?", "What yield does an eleven-month CD pay?"),
    ]
    return worlds, cases


def challenge_conflicts():
    return [
        ("expired-source", [[cd("4.58%", term=11), cd("8.76%", " (offer ended 2034-02-11)", "/promo", 11)]], [gold("certificate", ["4.58%", "8.76%"], "4.58%")]),
        ("zero-change", [[cd("0%", term=23)], [cd("3.87%", term=23)]], [gold("certificate", ["0%", "3.87%"], "3.87%")]),
        ("number-format", [[cd("4.230%", term=11)], [cd("4.23%", term=11)]], []),
        ("overlap", [[auto([("2030-33", 48, "5.12")])], [auto([("2029-31", 48, "5.75"), ("2032-35", 48, "4.92")])]], [
            gold("auto", ["5.12%", "5.75%"], "5.75%"), gold("auto", ["5.12%", "4.92%"], "4.92%")]),
        ("range-endpoint", [[cards([("Rewards Visa", "9.75% - 16.15%")])], [cards([("Rewards Visa", "9.75% - 18.60%")])]], [gold("rewards visa", ["9.75% - 16.15%", "9.75% - 18.60%"], "9.75% - 18.60%")]),
        ("moved-page", [[cd("3.14%", term=11)], [cd("4.62%", path="/certificates", term=11)]], [gold("certificate", ["3.14%", "4.62%"], "4.62%")]),
        ("product-isolation", [[cards([("Platinum Visa", "9.20% - 14.25%"), ("Rewards Visa", "11.90% - 17.10%")])]], []),
        ("ira-isolation", [[cds([("Traditional: 14 Month Fixed Jumbo Special", "4.21%", "4.33%", "$40,000"), ("Roth: 14 Month Fixed Jumbo Special", "4.46%", "4.55%", "$75,000")], "/personal/retirement", "IRA Certificate Rates")]], []),
        ("same-authority", [[cd("3.33%", path="/rates/a", term=11), cd("4.27%", path="/rates/b", term=11)]], [gold("certificate", ["3.33%", "4.27%"])]),
        ("removed-all", [[cd("4.18%", term=11)], [cd("4.39%", term=11)], [("/notice", "The 11-month certificate has been discontinued.")]], [gold("certificate", ["4.18%", "4.39%"])]),
        ("noise-isolation", [[("/resources", "![chart](https://challenge.test/20260419%20chart.png)\nAn illustrative payment is $1,365.")]], []),
        ("future-rate", [[cd("4.05%", term=11), cd("4.95%", "\nRates are effective as of 05/01/2034.", "/certificate-offer", 11)]], [gold("certificate", ["4.05%", "4.95%"], "4.05%")]),
    ]
