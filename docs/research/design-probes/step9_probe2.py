"""Step 9 probe 2: the number check on synthetic replies, some honest and some with a wrong number planted."""
from numcheck import check

BAL = {"balance": {"paise": 12487350, "text": "₹1,24,873.50"}, "as_of": "2026-10-31"}
SPEND = {"total": {"paise": 845000, "text": "₹8,450"}, "by_category": [{"name": "Food", "paise": 320000}, {"name": "Rent", "paise": 525000}], "row_count": 42, "month": "2026-10"}
PREPAY = {"interest_saved": {"paise": 18325000, "text": "₹1,83,250"}, "months_saved": 8, "new_emi": {"paise": 2245000, "text": "₹22,450"}, "prepay": {"paise": 5000000, "text": "₹50,000"}}
SIP = {"at_12": {"paise": 116169500, "text": "₹11,61,695"}, "at_8": {"paise": 91473000, "text": "₹9,14,730"}, "monthly": {"paise": 500000}, "years": 10}
PROJECT = {"verdict": "yes-but", "floor_probability": 0.87, "lowest": {"paise": 1820000, "text": "₹18,200", "date": "2026-11-28"}, "floor": {"paise": 2000000, "text": "₹20,000"}}
ROWS = {"columns": ["date", "payee", "amount_paise"], "rows": [["2026-10-03", "Swiggy", 45000], ["2026-10-09", "Zepto", 120000], ["2026-10-21", "Swiggy", 38000]], "row_count": 3}

# (label, reply, tool returns, your messages, the wrong numbers it should catch)
CASES = [
    # Honest replies: nothing should be flagged.
    ("plain balance", "Your SBI balance is ₹1,24,873.50 as of 31 October.", BAL, [], []),
    ("rounded in lakh", "You have about ₹1.25 lakh in SBI.", BAL, [], []),
    ("rounded to rupees", "SBI: ₹1,24,874.", BAL, [], []),
    ("Hinglish", "Is mahine ka total ₹8,450 hai, jisme Rent ₹5,250 aur Food ₹3,200 hai.", SPEND, [], []),
    ("short thousands", "Food pe ₹3.2k gaya, Rent ₹5.25k.", SPEND, [], []),
    ("a what-if", "Prepaying ₹50,000 saves ₹1,83,250 in interest and ends the loan 8 months early. Your new EMI would be ₹22,450.", PREPAY, [], []),
    ("user's assumption", "₹5,000 a month at an assumed 12% grows to ₹11,61,695 in 10 years; at 8%, ₹9,14,730.", SIP, ["what if I put 5000 a month at 12% or 8% for 10 years"], []),
    ("a verdict", "Yes, but tight: an 87% chance of staying above your ₹20,000 floor, with a low of ₹18,200 on 28 November.", PROJECT, [], []),
    ("percent as written", "There's an 87 percent chance you stay above the floor.", PROJECT, [], []),
    ("echoing you", "Logged ₹450 for chai.", {"status": "saved", "undo_id": "u1"}, ["chai 450"], []),
    ("lakh you typed", "Saved a goal of ₹2 lakh by March 2027.", {"status": "saved"}, ["goal 2 lakh by march 2027"], []),
    ("a numbered list", "Top payees:\n1. Swiggy ₹450\n2. Zepto ₹1,200\n3. Swiggy ₹380", ROWS, [], []),
    ("row count", "I found 3 transactions.", ROWS, [], []),
    ("dates and a time", "Your statement from 3 October 2026 was imported on 2026-10-31 at 9:30.", {"status": "review"}, [], []),
    # Honest, but the model counted or did easy arithmetic itself: the check should flag these as untraced.
    ("counted by eye", "Two of those were Swiggy.", ROWS, [], []),
    ("counted in digits", "2 of those were Swiggy.", ROWS, [], ["2"]),
    ("years from months", "That's 8 months, under 1 year.", PREPAY, [], ["1"]),
    # Wrong numbers planted: each should be caught.
    ("wrong balance", "Your SBI balance is ₹1,42,873.50.", BAL, [], ["₹1,42,873.50"]),
    ("wrong in lakh", "You have about ₹1.52 lakh.", BAL, [], ["₹1.52 lakh"]),
    ("off by a factor", "Interest saved: ₹18,325.", PREPAY, [], ["₹18,325"]),
    ("wrong months", "It ends the loan 18 months early.", PREPAY, [], ["18"]),
    ("wrong percent", "A 78% chance of staying above the floor.", PROJECT, [], ["78%"]),
    ("Hinglish wrong", "Total ₹8,540 hai.", SPEND, [], ["₹8,540"]),
    ("invented with no tool", "You spent ₹12,000 on food last month.", SPEND, [], ["₹12,000"]),
    ("a sum done in the head", "Swiggy and Zepto come to ₹2,030.", ROWS, [], ["₹2,030"]),
    ("right number, wrong label", "Food was ₹5,250 and Rent ₹3,200.", SPEND, [], []),
]


def run(kinds, label):
    caught = missed = false_alarm = clean = 0
    details = []
    for name, reply, returns, user, planted in CASES:
        flagged = check(reply, returns, user, kinds=kinds)
        expected = [p for p in planted if not (p.isdigit() and "bare" not in kinds)]
        if expected:
            hit = all(any(p in f or f in p for f in flagged) for p in expected)
            caught += hit
            missed += not hit
            if not hit:
                details.append(f"MISSED {name}: {flagged}")
        elif flagged:
            false_alarm += 1
            details.append(f"FALSE ALARM {name}: {flagged}")
        else:
            clean += 1
    print(f"== {label}")
    print(f"   wrong numbers caught: {caught} | missed: {missed} | honest replies passed: {clean} | false alarms: {false_alarm}")
    for d in details:
        print("  ", d)


run(("money", "percent", "bare"), "All numbers")
run(("money", "percent"), "Money and percentages only")
