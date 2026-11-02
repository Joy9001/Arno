"""The number check, ported from the October 31 probe's 26 synthetic replies."""

from __future__ import annotations

from fin_agent.agent.number_check import check

BAL = {"balance": {"paise": 12487350, "text": "₹1,24,873.50"}, "as_of": "2026-10-31"}
SPEND = {
    "total": {"paise": 845000, "text": "₹8,450"},
    "by_category": [{"name": "Food", "paise": 320000}, {"name": "Rent", "paise": 525000}],
    "row_count": 42,
    "month": "2026-10",
}
PREPAY = {
    "interest_saved": {"paise": 18325000, "text": "₹1,83,250"},
    "months_saved": 8,
    "new_emi": {"paise": 2245000, "text": "₹22,450"},
    "prepay": {"paise": 5000000, "text": "₹50,000"},
}
SIP = {
    "at_12": {"paise": 116169500, "text": "₹11,61,695"},
    "at_8": {"paise": 91473000, "text": "₹9,14,730"},
    "monthly": {"paise": 500000},
    "years": 10,
}
PROJECT = {
    "verdict": "yes-but",
    "floor_probability": 0.87,
    "lowest": {"paise": 1820000, "text": "₹18,200", "date": "2026-11-28"},
    "floor": {"paise": 2000000, "text": "₹20,000"},
}
ROWS = {
    "columns": ["date", "payee", "amount_paise"],
    "rows": [
        ["2026-10-03", "Swiggy", 45000],
        ["2026-10-09", "Zepto", 120000],
        ["2026-10-21", "Swiggy", 38000],
    ],
    "row_count": 3,
}

CASES = [
    ("plain balance", "Your SBI balance is ₹1,24,873.50 as of 31 October.", BAL, [], []),
    ("rounded in lakh", "You have about ₹1.25 lakh in SBI.", BAL, [], []),
    ("rounded to rupees", "SBI: ₹1,24,874.", BAL, [], []),
    (
        "Hinglish",
        "Is mahine ka total ₹8,450 hai, jisme Rent ₹5,250 aur Food ₹3,200 hai.",
        SPEND,
        [],
        [],
    ),
    ("short thousands", "Food pe ₹3.2k gaya, Rent ₹5.25k.", SPEND, [], []),
    (
        "a what-if",
        "Prepaying ₹50,000 saves ₹1,83,250 in interest and ends the loan 8 months early. "
        "Your new EMI would be ₹22,450.",
        PREPAY,
        [],
        [],
    ),
    (
        "user's assumption",
        "₹5,000 a month at an assumed 12% grows to ₹11,61,695 in 10 years; at 8%, ₹9,14,730.",
        SIP,
        ["what if I put 5000 a month at 12% or 8% for 10 years"],
        [],
    ),
    (
        "a verdict",
        "Yes, but tight: an 87% chance of staying above your ₹20,000 floor, with a low of "
        "₹18,200 on 28 November.",
        PROJECT,
        [],
        [],
    ),
    (
        "percent as written",
        "There's an 87 percent chance you stay above the floor.",
        PROJECT,
        [],
        [],
    ),
    (
        "echoing you",
        "Logged ₹450 for chai.",
        {"status": "saved", "undo_id": "u1"},
        ["chai 450"],
        [],
    ),
    (
        "lakh you typed",
        "Saved a goal of ₹2 lakh by March 2027.",
        {"status": "saved"},
        ["goal 2 lakh by march 2027"],
        [],
    ),
    (
        "a numbered list",
        "Top payees:\n1. Swiggy ₹450\n2. Zepto ₹1,200\n3. Swiggy ₹380",
        ROWS,
        [],
        [],
    ),
    ("row count", "I found 3 transactions.", ROWS, [], []),
    (
        "dates and a time",
        "Your statement from 3 October 2026 was imported on 2026-10-31 at 9:30.",
        {"status": "review"},
        [],
        [],
    ),
    ("counted by eye", "Two of those were Swiggy.", ROWS, [], []),
    ("counted in digits", "2 of those were Swiggy.", ROWS, [], ["2"]),
    ("years from months", "That's 8 months, under 1 year.", PREPAY, [], ["1"]),
    ("wrong balance", "Your SBI balance is ₹1,42,873.50.", BAL, [], ["₹1,42,873.50"]),
    ("wrong in lakh", "You have about ₹1.52 lakh.", BAL, [], ["₹1.52 lakh"]),
    ("off by a factor", "Interest saved: ₹18,325.", PREPAY, [], ["₹18,325"]),
    ("wrong months", "It ends the loan 18 months early.", PREPAY, [], ["18"]),
    ("wrong percent", "A 78% chance of staying above the floor.", PROJECT, [], ["78%"]),
    ("Hinglish wrong", "Total ₹8,540 hai.", SPEND, [], ["₹8,540"]),
    ("invented with no tool", "You spent ₹12,000 on food last month.", SPEND, [], ["₹12,000"]),
    ("a sum done in the head", "Swiggy and Zepto come to ₹2,030.", ROWS, [], ["₹2,030"]),
    # A real number used with the wrong label is an eval's job, not this check's.
    ("right number, wrong label", "Food was ₹5,250 and Rent ₹3,200.", SPEND, [], []),
]


def test_all_number_check_cases_with_bare_counts():
    for name, reply, returns, user, planted in CASES:
        flagged = check(reply, returns, tuple(user))
        if planted:
            assert all(any(p in f or f in p for f in flagged) for p in planted), (
                f"{name}: expected {planted}, got {flagged}"
            )
        else:
            assert not flagged, f"{name}: false alarm {flagged}"


def test_money_and_percent_only_ignores_bare_numbers():
    assert check("2 of those were Swiggy.", ROWS, kinds=("money", "percent")) == []
    assert check("You have about ₹1.52 lakh.", BAL, kinds=("money", "percent")) == ["₹1.52 lakh"]


def test_a_pinned_snapshot_counts_as_a_source():
    from pydantic_ai.messages import ModelRequest
    from pydantic_ai_harness.compaction import pin

    from fin_agent.agent.number_check import check_messages

    snapshot = ModelRequest(parts=[pin('{"balance": {"paise": 12487350}}')])
    assert check_messages("Your SBI balance is ₹1,24,873.50.", [snapshot]) == []


def test_month_dates_and_narrow_spaces_are_not_bare_numbers():
    assert check("October 2026 and 2026-10 are the period.", {}) == []
    assert check("August **2025** has no entries.", {}) == []
    assert check("Coverage is 0\u202f%.", {}) == ["0 %"]
    assert check("Coverage is 0\u202f%.", {"coverage": {"complete": False}}) == []


def test_a_day_range_tied_to_a_month_is_a_date():
    assert check("October (1–31) is covered.", {}) == []
    assert check("Coverage runs 1–31 October.", {}) == []
    assert check("The period is 1-31 May.", {}) == []
    assert check("I found 31 transactions.", {}) == ["31"]


def test_a_number_from_a_tool_inside_run_code_is_traced():
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    from fin_agent.agent.number_check import check_messages

    nested = ToolReturnPart(
        tool_name="summarize_spending",
        content={"total": {"paise": 155000}, "groups": [{"key": "Swiggy", "count": 4}]},
        tool_call_id="call-inner",
    )
    outer = ToolReturnPart(
        tool_name="run_code",
        content="The script ran.",
        tool_call_id="call-outer",
        metadata={"tool_returns": {"call-inner": nested}},
    )
    assert check_messages("Food was ₹1,550 over 4 orders.", [ModelRequest(parts=[outer])]) == []


def test_query_movements_rows_are_traced():
    payload = {
        "rows": [
            {
                "date": "2026-10-09",
                "amount": {"paise": 62000, "text": "₹620"},
                "amount_paise": 62000,
                "transfers": [{"amount": {"paise": 40000, "text": "₹400"}, "amount_paise": 40000}],
            },
            {
                "date": "2026-10-15",
                "amount": {"paise": 80000, "text": "₹800"},
                "amount_paise": 80000,
            },
        ],
        "total_count": 2,
    }
    assert check("Swiggy ₹620; the dinner bill was ₹800 and my share ₹400.", payload) == []


def test_a_summed_paise_column_is_traced_at_its_rupee_value():
    result = {
        "columns": ["month", "spend"],
        "rows": [["2026-08", 5864900]],
        "sql": "select to_char(date,'YYYY-MM') AS month, sum(amount_paise) AS spend ...",
        "row_count": 1,
    }
    assert check("August 2026 was ₹58,649.", result) == []
    assert check("August 2026 was ₹58,649.", result, kinds=("money",)) == []


def test_money_only_ignores_hindi_style_days_and_months():
    reply = "No data for August 2025. The next EMI is on 10 tareekh, about 30 din away."
    assert check(reply, {}, kinds=("money",)) == []
    assert check(reply, {"days": 30, "day": 10}, kinds=("money",)) == []


# Regressions from the first full L4 run on the demo persona (November 2, 2026). Each reply or
# script output below is the real text that was flagged, and none of these numbers was invented.


def test_script_output_values_survive_month_words_on_the_next_line():
    output = "Aug per day: ₹46.77\nSep per day: ₹48.33\nOct per day: ₹50.0\n"
    reply = "Per-day rough average: ₹46.77 (Aug), ₹48.33 (Sep), ₹50.00 (Oct)."
    assert check(reply, [], scripted=[{"output": output}]) == []
    output = "oct vs sep pct 6.9\ntotal 4450.0\navg 1483.33\noct gross before refund 1850.0\n"
    assert (
        check("3-month average ₹1,483.33, total ₹4,450.", [], scripted=[{"output": output}]) == []
    )


def test_a_script_s_negative_and_paise_values_are_traced():
    scripted = [{"output": "net worth: -1804797.0\navg paise 5943233.333333333\n"}]
    assert check("Net worth **-₹18,04,797**.", [], scripted=scripted) == []
    assert check("Average ₹59,432/month.", [], scripted=scripted) == []
    # Only a script's output is read as paise; a tool's plain string is not.
    assert check("Average ₹59,432/month.", [{"note": "avg 5943233.33"}]) == ["₹59,432"]


def test_tool_arguments_are_sources_but_script_code_is_not():
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

    from fin_agent.agent.number_check import check_messages

    direct = ModelResponse(
        parts=[ToolCallPart("compound", {"monthly_paise": 500000, "months": 60}, "c1")]
    )
    assert check_messages("₹5,000 a month for 60 months.", [direct]) == []

    nested_call = ToolCallPart("compound", {"months": 86, "start_paise": 3000000}, "c2__1")
    script = ModelRequest(
        parts=[
            ToolReturnPart(
                "run_code",
                {"output": "done"},
                "c2",
                metadata={"tool_calls": {"c2__1": nested_call}, "tool_returns": {}},
            )
        ]
    )
    assert check_messages("It crosses ₹12 lakh in 86 months.", [script]) == ["₹12 lakh"]

    typed = ModelResponse(parts=[ToolCallPart("run_code", {"code": "x = 40000\nx"}, "c3")])
    assert check_messages("You will have ₹40,000.", [typed]) == ["₹40,000"]


def test_dates_times_lists_and_labels_are_not_quantities():
    replies = [
        "Chaho to 9 November ko Ravi ke liye, 16 ko Amit ke liye reminder set kar du?",
        "Aapka monthly contribution har mahine ki 7 tarikh ko hai.",
        "Top 3:\n- Rent\n- Family Transfer\n- Groceries",
        "**1. Six months ka expense**\n### 2. Kab tak",
        "Form 16 ya AIS/26AS ho to woh bhi daalo; section 24(b) ka claim bhi.",
        "Ledger sirf last 4 digits store karta hai (e.g. •••• 1234), 16-digit number nahi.",
        "Woh payee 2026-11-02 14:28:52 par bana, ya 2026-11-02T14:39:54Z.",
        "Last year matlab calendar 2025, ya FY 2025-26 (Apr 2025–Mar 2026).",
    ]
    for reply in replies:
        assert check(reply, {}) == [], reply
    assert check("Ramesh ko ₹16 ko diya?", {}) == ["₹16"]


def test_correct_prose_arithmetic_is_derived_not_invented():
    from fin_agent.agent.number_check import classify

    goal = {"saved": {"paise": 3_000_000}, "monthly": {"paise": 1_000_000}}
    reply = "Saved ₹30,000; after the next ₹10,000 it becomes ₹40,000."
    assert classify(reply, goal) == [("₹40,000", "derived")]
    assert check(reply, goal) == ["₹40,000"]  # still a flag: the rule says compute in a script
    assert classify("Saved ₹30,000, so ₹45,000 by March.", goal) == [("₹45,000", "untraced")]
