"""Turn-local result registry and the composed-answer contract.

Read and calculation tools register a bounded, self-contained snapshot here. The model sees a
small ``ui_results`` descriptor and may compose those snapshots with ``present``. The registry is
per turn: an ID from an earlier turn is stale, and a missing or unsupported view is a retry, never
a guessed number or a zero.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fin_agent.engine.money import money

MAX_TABLE_ROWS = 50
MAX_TABLE_COLUMNS = 12
MAX_PRESENT_NODES = 12
MAX_PRESENT_DEPTH = 3
MAX_REGISTERED_RESULTS = 64
MAX_SOURCE_RESULTS = 48

LEAF_TYPES = frozenset({"Summary", "Table", "Chart", "Receipt"})
CONTAINER_TYPES = frozenset({"Stack", "Comparison"})
ANSWER_TYPES = LEAF_TYPES | CONTAINER_TYPES

Cell = dict[str, Any]
Column = dict[str, str]


class PresentationError(ValueError):
    """A result ID, view, or table shape the model must fix."""


def _unknown_cell() -> Cell:
    return {"type": "unknown"}


def _money_cell(value: Any) -> Cell:
    if value is None or isinstance(value, bool):
        return _unknown_cell()
    if isinstance(value, dict):
        value = value.get("paise")
    if value is None:
        return _unknown_cell()
    try:
        paise = int(value)
    except TypeError, ValueError, OverflowError:
        return _unknown_cell()
    rendered = money(paise)
    return {"type": "money", "paise": paise, "text": rendered["text"] if rendered else ""}


def _number_cell(value: Any) -> Cell:
    if value is None or isinstance(value, bool):
        return _unknown_cell()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise PresentationError("Table numbers must be finite; use null for unknown values.")
        return {"type": "number", "value": value}
    return _text_cell(value)


def _text_cell(value: Any) -> Cell:
    if value is None:
        return _unknown_cell()
    return {"type": "text", "text": str(value)}


def _coverage_notes(coverage: dict[str, Any] | None) -> list[str]:
    if not coverage:
        return []
    gaps = coverage.get("gaps") or []
    if not gaps:
        return []
    return [f"No statement covers {', '.join(str(gap) for gap in gaps)}."]


def _period_label(period: dict[str, Any] | None) -> str:
    if not period:
        return "the period"
    start = period.get("from")
    end = period.get("to")
    if start and end:
        return f"{start} to {end}"
    if period.get("label"):
        return str(period["label"])
    return "the period"


def _table(
    *,
    caption: str,
    columns: list[Column],
    rows: list[list[Cell]],
    total_rows: int | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    total = total_rows if total_rows is not None else len(rows)
    return {
        "caption": caption,
        "columns": columns,
        "rows": rows,
        "shown_rows": len(rows),
        "total_rows": total,
        "truncated": total > len(rows),
        "note": note,
    }


def _summary(facts: list[dict[str, Any]], description: str | None = None) -> dict[str, Any]:
    return {"facts": facts, "description": description}


def _snapshot(
    *,
    kind: str,
    title: str,
    views: list[str],
    summary: dict[str, Any] | None = None,
    table: dict[str, Any] | None = None,
    chart: dict[str, Any] | None = None,
    receipt: dict[str, Any] | None = None,
    period: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    notes: list[str] | None = None,
    source_ids: dict[str, list[str]] | None = None,
    as_of: str | None = None,
    sql: str | None = None,
    assumptions: list[str] | None = None,
    empty: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "views": views,
        "empty": empty,
        "notes": notes or [],
        "as_of": as_of,
    }
    optional = {
        "summary": summary,
        "table": table,
        "chart": chart,
        "receipt": receipt,
        "period": period,
        "filters": filters,
        "coverage": coverage,
        "source_ids": source_ids,
        "sql": sql,
        "assumptions": assumptions,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    return payload


@dataclass(frozen=True)
class PresentationResult:
    result_id: str
    kind: str
    title: str
    views: tuple[str, ...]
    snapshot: dict[str, Any]

    def descriptor(self) -> dict[str, Any]:
        return {"id": self.result_id, "title": self.title, "views": list(self.views)}


@dataclass
class Registration:
    results: list[PresentationResult]
    fallback: dict[str, Any] | None = None

    @property
    def descriptors(self) -> list[dict[str, Any]]:
        return [result.descriptor() for result in self.results]

    @property
    def primary_id(self) -> str | None:
        return self.results[0].result_id if self.results else None


class ResultRegistry:
    """The current turn's results. IDs are opaque and never reused across turns."""

    def __init__(self) -> None:
        self._results: dict[str, PresentationResult] = {}
        self._scope = uuid4().hex
        self._next = 0

    def add(
        self, *, kind: str, title: str, views: list[str], snapshot: dict[str, Any]
    ) -> PresentationResult:
        if len(self._results) >= MAX_REGISTERED_RESULTS:
            raise PresentationError("Too many presentation results in one turn; reuse ui_results.")
        self._next += 1
        result_id = f"res_{self._scope}_{self._next}"
        body = copy.deepcopy(snapshot)
        body.update(
            {
                "result_id": result_id,
                "kind": kind,
                "title": title,
                "views": list(views),
            }
        )
        result = PresentationResult(
            result_id=result_id,
            kind=kind,
            title=title,
            views=tuple(views),
            snapshot=body,
        )
        self._results[result_id] = result
        return replace(result, snapshot=copy.deepcopy(body))

    def get(self, result_id: str) -> PresentationResult:
        result = self._results.get(result_id)
        if result is None:
            raise PresentationError(
                f"Unknown or stale result id {result_id!r}. ui_results ids last for this turn; "
                "read the data again and use a current id."
            )
        return replace(result, snapshot=copy.deepcopy(result.snapshot))

    def register_read(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> Registration:
        context = context or {}
        if kind == "context":
            return Registration(self._register_context(payload))
        builder = {
            "spending": self._register_spending,
            "movements": self._register_movements,
            "query": self._register_query,
            "project": self._register_projection,
        }.get(kind)
        if builder is None:  # pragma: no cover - internal misuse
            raise PresentationError(f"Unknown result kind {kind!r}")
        result = builder(payload, context)
        fallback = _fallback_card(result) if kind in {"spending", "movements", "query"} else None
        return Registration([result], fallback)

    def register_calculation(
        self, kind: str, payload: dict[str, Any], card: dict[str, Any]
    ) -> PresentationResult:
        builder = {
            "amortize": self._register_amortize,
            "compound": self._register_compound,
            "chart": self._register_chart,
        }.get(kind)
        if builder is None:  # pragma: no cover - internal misuse
            raise PresentationError(f"Unknown calculation kind {kind!r}")
        return builder(payload, card)

    def register_table(self, table: TableInput) -> PresentationResult:
        columns = [
            {"key": str(index), **column.model_dump()} for index, column in enumerate(table.columns)
        ]
        rows = [_table_row(table.columns, row) for row in table.rows]
        sources = self._table_sources(table.source_result_ids)
        coverage = _source_coverage(sources)
        notes = _inherited_notes(sources)
        assumptions = list(
            dict.fromkeys(note for source in sources for note in source.get("assumptions", []))
        )
        source_ids: dict[str, list[str]] = {}
        for source in sources:
            for kind, ids in source.get("source_ids", {}).items():
                source_ids[kind] = list(dict.fromkeys([*source_ids.get(kind, []), *ids]))
        snapshot = _snapshot(
            kind="table",
            title=table.title,
            views=["Table", "Summary"],
            summary=_summary(
                [
                    {"label": "Rows", "value": _number_cell(len(rows))},
                    {"label": "Columns", "value": _number_cell(len(columns))},
                ]
            ),
            table=_table(caption=table.title, columns=columns, rows=rows),
            as_of=table.as_of,
            source_ids=source_ids or None,
            coverage=coverage,
            notes=notes,
            assumptions=assumptions or None,
            empty=not rows,
        )
        snapshot["sources"] = sources
        return self.add(
            kind="table", title=table.title, views=["Table", "Summary"], snapshot=snapshot
        )

    def _table_sources(self, result_ids: list[str]) -> list[dict[str, Any]]:
        """Keep a flat lineage of evidence metadata, including earlier derived tables."""
        sources: dict[str, dict[str, Any]] = {}
        evidence_keys = (
            "period",
            "filters",
            "as_of",
            "coverage",
            "notes",
            "assumptions",
            "source_ids",
            "sql",
        )
        for result_id in result_ids:
            result = self.get(result_id)
            snapshot = result.snapshot
            sources[result_id] = {
                **result.descriptor(),
                **{key: snapshot[key] for key in evidence_keys if key in snapshot},
            }
            for ancestor in snapshot.get("sources", []):
                sources.setdefault(ancestor["id"], ancestor)
        if len(sources) > MAX_SOURCE_RESULTS:
            raise PresentationError(
                "Too many source results for one table; use a smaller comparison."
            )
        return list(sources.values())

    # Per-kind builders ------------------------------------------------------------------

    def _register_spending(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> PresentationResult:
        period = payload.get("period") or {}
        group_by = str(context.get("group_by") or "category")
        group_label = "Total" if group_by == "none" else group_by.replace("_", " ").title()
        groups = payload.get("groups") or []
        shown = groups[:MAX_TABLE_ROWS]
        rows = [
            [
                _text_cell(group.get("key")),
                _money_cell(group.get("amount")),
                _number_cell(group.get("count")),
            ]
            for group in shown
        ]
        notes = _coverage_notes(payload.get("coverage"))
        if len(groups) > MAX_TABLE_ROWS:
            notes.append(f"Showing the first {MAX_TABLE_ROWS} of {len(groups)} groups.")
        views = ["Summary", "Table"]
        chart = None
        if groups and group_by != "none":
            views.append("Chart")
            chart = {
                "type": "bar",
                "title": f"Spending by {group_label}",
                "unit": "paise",
                "as_of": period.get("to"),
                "computed_at": datetime.now(UTC).isoformat(),
                "source": (
                    f"Spending totals returned by summarize_spending for {_period_label(period)}."
                ),
                "series": [
                    {
                        "name": group_label,
                        "points": [
                            {"x": str(group.get("key")), "y": int(group["amount"]["paise"])}
                            for group in shown
                            if isinstance(group.get("amount"), dict)
                            and group["amount"].get("paise") is not None
                        ],
                    }
                ],
            }
        title = f"Spending by {group_label}, {_period_label(period)}"
        snapshot = _snapshot(
            kind="spending",
            title=title,
            views=views,
            summary=_summary(
                [
                    {"label": "Total", "value": _money_cell(payload.get("total"))},
                    {"label": "Period", "value": _text_cell(_period_label(period))},
                    {
                        "label": "Groups",
                        "value": _number_cell(len(groups)),
                    },
                ]
            ),
            table=_table(
                caption=title,
                columns=[
                    {"key": "group", "label": group_label, "type": "text"},
                    {"key": "amount", "label": "Amount", "type": "money"},
                    {"key": "count", "label": "Entries", "type": "number"},
                ],
                rows=rows,
                total_rows=len(groups),
                note=notes[-1] if notes else None,
            ),
            chart=chart,
            period=period,
            filters=context.get("filters"),
            coverage=payload.get("coverage"),
            notes=notes,
            as_of=period.get("to"),
            empty=not groups,
        )
        return self.add(kind="spending", title=title, views=views, snapshot=snapshot)

    def _register_movements(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> PresentationResult:
        rows = payload.get("rows") or []
        shown = rows[:MAX_TABLE_ROWS]
        period = context.get("period") or {}
        title = f"Movements, {_period_label(period)}" if period else "Movements"
        notes = _coverage_notes(payload.get("coverage"))
        if len(rows) > MAX_TABLE_ROWS:
            notes.append(f"Showing the first {MAX_TABLE_ROWS} of {len(rows)} rows.")
        if payload.get("next_cursor"):
            notes.append("More rows are available; ask for the next page.")
        facts = [
            {"label": "Entries", "value": _number_cell(payload.get("total_count"))},
            {"label": "Shown", "value": _number_cell(len(shown))},
        ]
        if period:
            facts.append({"label": "Period", "value": _text_cell(_period_label(period))})
        source_ids = [str(row["id"]) for row in shown if row.get("id")]
        snapshot = _snapshot(
            kind="movements",
            title=title,
            views=["Summary", "Table"],
            summary=_summary(facts),
            table=_table(
                caption=title,
                columns=[
                    {"key": "date", "label": "Date", "type": "date"},
                    {"key": "description", "label": "Description", "type": "text"},
                    {"key": "payee", "label": "Payee", "type": "text"},
                    {"key": "category", "label": "Category", "type": "text"},
                    {"key": "amount", "label": "Amount", "type": "money"},
                ],
                rows=[
                    [
                        _text_cell(row.get("date")),
                        _text_cell(row.get("description")),
                        _text_cell(row.get("payee")),
                        _text_cell(row.get("category")),
                        _money_cell(row.get("amount_paise")),
                    ]
                    for row in shown
                ],
                total_rows=int(payload.get("total_count") or len(shown)),
                note=notes[-1] if notes else None,
            ),
            period=period or None,
            filters=context.get("filters"),
            coverage=payload.get("coverage"),
            notes=notes,
            source_ids={"movements": source_ids} if source_ids else None,
            empty=not rows,
        )
        return self.add(
            kind="movements", title=title, views=["Summary", "Table"], snapshot=snapshot
        )

    def _register_query(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> PresentationResult:
        del context
        all_columns = [str(name) for name in payload.get("columns") or []]
        all_rows = payload.get("rows") or []
        columns = all_columns[:MAX_TABLE_COLUMNS]
        shown_rows = all_rows[:MAX_TABLE_ROWS]
        types = [
            _column_type(columns[index], [row[index] for row in all_rows if index < len(row)])
            for index in range(len(columns))
        ]
        column_defs = [
            {"key": str(index), "label": name, "type": types[index]}
            for index, name in enumerate(columns)
        ]
        rows = [
            [
                _cell_for_type(types[index], row[index] if index < len(row) else None)
                for index in range(len(columns))
            ]
            for row in shown_rows
        ]
        total = int(payload.get("row_count") or len(all_rows))
        notes: list[str] = []
        if len(all_rows) > MAX_TABLE_ROWS:
            notes.append(f"Showing the first {MAX_TABLE_ROWS} of {total} rows.")
        if payload.get("truncated"):
            notes.append("The query returned more than 1000 rows; only the first 1000 were read.")
        if len(all_columns) > MAX_TABLE_COLUMNS:
            notes.append(f"Showing the first {MAX_TABLE_COLUMNS} of {len(all_columns)} columns.")
        snapshot = _snapshot(
            kind="query",
            title="Query result",
            views=["Summary", "Table"],
            summary=_summary(
                [
                    {"label": "Rows", "value": _number_cell(total)},
                    {"label": "Columns", "value": _number_cell(len(all_columns))},
                    {
                        "label": "Truncated",
                        "value": _text_cell("Yes" if payload.get("truncated") else "No"),
                    },
                ]
            ),
            table=_table(
                caption="Read-only query result",
                columns=column_defs,
                rows=rows,
                total_rows=total,
                note=notes[0] if notes else None,
            ),
            notes=notes,
            sql=payload.get("sql"),
            empty=not all_rows,
        )
        return self.add(
            kind="query", title="Query result", views=["Summary", "Table"], snapshot=snapshot
        )

    def _register_projection(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> PresentationResult:
        del payload
        card = context.get("card")
        if not isinstance(card, dict):
            card = {}
        goals = card.get("goals") or []
        shown_goals = goals[:MAX_TABLE_ROWS]
        title = "Affordability" if card.get("verdict") else "Projection"
        data_quality = card.get("data_quality") or {}
        notes = [
            *[str(note) for note in data_quality.get("gaps") or []],
            *[str(note) for note in data_quality.get("setup_gaps") or []],
        ]
        if len(goals) > MAX_TABLE_ROWS:
            notes.append(f"Showing the first {MAX_TABLE_ROWS} of {len(goals)} goals.")
        facts = [
            {
                "label": "Floor probability",
                "value": _text_cell(
                    f"{math.floor(float(card['floor_probability']) * 100 + 0.5)}%"
                    if card.get("floor_set") and card.get("floor_probability") is not None
                    else None
                ),
            },
            {
                "label": "Median low",
                "value": _money_cell((card.get("median_low") or {}).get("amount")),
            },
            {
                "label": "Floor",
                "value": (
                    _money_cell(card.get("floor"))
                    if card.get("floor_set")
                    else _text_cell("No floor set")
                ),
            },
            {"label": "Horizon", "value": _text_cell(f"{card.get('horizon_days')} days")},
        ]
        if card.get("verdict"):
            verdict = {
                "yes": "Yes",
                "yes-but": "Yes, with caution",
                "no": "No",
            }.get(str(card["verdict"]), str(card["verdict"]))
            facts.append({"label": "Verdict", "value": _text_cell(verdict)})
        snapshot = _snapshot(
            kind="projection",
            title=title,
            views=["Receipt", "Summary", "Table"],
            summary=_summary(facts),
            table=_table(
                caption="Goals in this projection",
                columns=[
                    {"key": "name", "label": "Goal", "type": "text"},
                    {"key": "target", "label": "Target", "type": "money"},
                    {"key": "saved", "label": "Saved", "type": "money"},
                    {"key": "remaining", "label": "Remaining", "type": "money"},
                    {"key": "deadline", "label": "Deadline", "type": "date"},
                    {"key": "completes_on", "label": "Completes on", "type": "date"},
                ],
                rows=[
                    [
                        _text_cell(goal.get("name")),
                        _money_cell(goal.get("target")),
                        _money_cell(goal.get("saved")),
                        _money_cell(goal.get("remaining")),
                        _text_cell(goal.get("deadline")),
                        _text_cell(goal.get("completes_on")),
                    ]
                    for goal in shown_goals
                ],
                total_rows=len(goals),
            ),
            receipt=card,
            notes=notes,
            source_ids=card.get("source_ids"),
            as_of=card.get("as_of"),
        )
        return self.add(
            kind="projection",
            title=title,
            views=["Receipt", "Summary", "Table"],
            snapshot=snapshot,
        )

    def _register_context(self, payload: dict[str, Any]) -> list[PresentationResult]:
        today = payload.get("today")
        warnings = [str(note) for note in payload.get("warnings") or []]
        results = [self._register_accounts(payload, today, warnings)]
        results.append(self._register_upcoming(payload, today))
        results.append(self._register_goals(payload, today))
        results.append(self._register_owed(payload, today))
        return results

    def _register_accounts(
        self,
        payload: dict[str, Any],
        today: str | None,
        warnings: list[str],
    ) -> PresentationResult:
        accounts = payload.get("accounts") or []
        shown_accounts = accounts[:MAX_TABLE_ROWS]
        rows = [
            [
                _text_cell(account.get("name")),
                _text_cell(account.get("kind")),
                _money_cell(account.get("balance")),
                _text_cell(account.get("as_of")),
                _text_cell(
                    "No gaps"
                    if (account.get("covered") or {}).get("gaps") == []
                    else ", ".join((account.get("covered") or {}).get("gaps") or [])
                ),
            ]
            for account in shown_accounts
        ]
        notes = list(warnings)
        if len(accounts) > MAX_TABLE_ROWS:
            notes.append(f"Showing the first {MAX_TABLE_ROWS} of {len(accounts)} accounts.")
        snapshot = _snapshot(
            kind="accounts",
            title="Accounts",
            views=["Summary", "Table"],
            summary=_summary(
                [
                    {"label": "Liquid total", "value": _money_cell(payload.get("liquid_total"))},
                    {"label": "Accounts", "value": _number_cell(len(accounts))},
                ]
            ),
            table=_table(
                caption="Accounts and stated balances",
                columns=[
                    {"key": "name", "label": "Account", "type": "text"},
                    {"key": "kind", "label": "Kind", "type": "text"},
                    {"key": "balance", "label": "Balance", "type": "money"},
                    {"key": "as_of", "label": "As of", "type": "date"},
                    {"key": "coverage", "label": "Statement gaps", "type": "text"},
                ],
                rows=rows,
                total_rows=len(accounts),
                note=notes[-1] if notes else None,
            ),
            notes=notes,
            source_ids={
                "accounts": [str(account["id"]) for account in shown_accounts if account.get("id")]
            },
            as_of=today,
            empty=not accounts,
        )
        return self.add(
            kind="accounts", title="Accounts", views=["Summary", "Table"], snapshot=snapshot
        )

    def _register_upcoming(self, payload: dict[str, Any], today: str | None) -> PresentationResult:
        events = payload.get("upcoming") or []
        shown_events = events[:MAX_TABLE_ROWS]
        first = events[0] if events else None
        notes = []
        if len(events) > MAX_TABLE_ROWS:
            notes.append(f"Showing the first {MAX_TABLE_ROWS} of {len(events)} events.")
        snapshot = _snapshot(
            kind="upcoming",
            title="Upcoming, next 30 days",
            views=["Summary", "Table"],
            summary=_summary(
                [
                    {"label": "Events", "value": _number_cell(len(events))},
                    {
                        "label": "Next",
                        "value": _text_cell(
                            f"{first.get('date')} · {first.get('name')}" if first else None
                        ),
                    },
                ]
            ),
            table=_table(
                caption="Scheduled and due in the next 30 days",
                columns=[
                    {"key": "date", "label": "Date", "type": "date"},
                    {"key": "name", "label": "Name", "type": "text"},
                    {"key": "kind", "label": "Kind", "type": "text"},
                    {"key": "amount", "label": "Amount", "type": "money"},
                    {"key": "confirmed", "label": "Confirmed", "type": "text"},
                ],
                rows=[
                    [
                        _text_cell(event.get("date")),
                        _text_cell(event.get("name")),
                        _text_cell(event.get("kind")),
                        _money_cell(event.get("amount")),
                        _text_cell("Yes" if event.get("confirmed") else "No"),
                    ]
                    for event in shown_events
                ],
                total_rows=len(events),
                note=notes[-1] if notes else None,
            ),
            notes=notes,
            source_ids={
                "events": [
                    str(event["source_id"]) for event in shown_events if event.get("source_id")
                ]
            },
            as_of=today,
            empty=not events,
        )
        return self.add(
            kind="upcoming",
            title="Upcoming, next 30 days",
            views=["Summary", "Table"],
            snapshot=snapshot,
        )

    def _register_goals(self, payload: dict[str, Any], today: str | None) -> PresentationResult:
        goals = payload.get("goals") or []
        shown_goals = goals[:MAX_TABLE_ROWS]
        notes = []
        if len(goals) > MAX_TABLE_ROWS:
            notes.append(f"Showing the first {MAX_TABLE_ROWS} of {len(goals)} goals.")
        snapshot = _snapshot(
            kind="goals",
            title="Goals",
            views=["Summary", "Table"],
            summary=_summary([{"label": "Goals", "value": _number_cell(len(goals))}]),
            table=_table(
                caption="Active goals",
                columns=[
                    {"key": "name", "label": "Goal", "type": "text"},
                    {"key": "target", "label": "Target", "type": "money"},
                    {"key": "saved", "label": "Saved", "type": "money"},
                    {"key": "remaining", "label": "Remaining", "type": "money"},
                    {"key": "deadline", "label": "Deadline", "type": "date"},
                    {"key": "status", "label": "Status", "type": "text"},
                ],
                rows=[
                    [
                        _text_cell(goal.get("name")),
                        _money_cell(goal.get("target")),
                        _money_cell(goal.get("saved")),
                        _money_cell(goal.get("remaining")),
                        _text_cell(goal.get("deadline")),
                        _text_cell(goal.get("status")),
                    ]
                    for goal in shown_goals
                ],
                total_rows=len(goals),
                note=notes[-1] if notes else None,
            ),
            notes=notes,
            source_ids={"goals": [str(goal["id"]) for goal in shown_goals if goal.get("id")]},
            as_of=today,
            empty=not goals,
        )
        return self.add(kind="goals", title="Goals", views=["Summary", "Table"], snapshot=snapshot)

    def _register_owed(self, payload: dict[str, Any], today: str | None) -> PresentationResult:
        owed = payload.get("owed") or []
        shown_owed = owed[:MAX_TABLE_ROWS]
        notes = []
        if len(owed) > MAX_TABLE_ROWS:
            notes.append(f"Showing the first {MAX_TABLE_ROWS} of {len(owed)} people.")
        snapshot = _snapshot(
            kind="owed",
            title="Owed",
            views=["Summary", "Table"],
            summary=_summary([{"label": "People", "value": _number_cell(len(owed))}]),
            table=_table(
                caption="Who owes what",
                columns=[
                    {"key": "person", "label": "Person", "type": "text"},
                    {"key": "amount", "label": "Amount", "type": "money"},
                    {"key": "direction", "label": "Direction", "type": "text"},
                    {"key": "expected", "label": "Expected", "type": "date"},
                ],
                rows=[
                    [
                        _text_cell(item.get("person")),
                        _money_cell(item.get("amount")),
                        _text_cell(item.get("direction")),
                        _text_cell(item.get("expected_date")),
                    ]
                    for item in shown_owed
                ],
                total_rows=len(owed),
                note=notes[-1] if notes else None,
            ),
            notes=notes,
            source_ids={
                "payees": [str(item["payee_id"]) for item in shown_owed if item.get("payee_id")]
            },
            as_of=today,
            empty=not owed,
        )
        return self.add(kind="owed", title="Owed", views=["Summary", "Table"], snapshot=snapshot)

    def _register_amortize(
        self, payload: dict[str, Any], card: dict[str, Any]
    ) -> PresentationResult:
        del card
        schedule = payload.get("schedule") or []
        shown = schedule[:MAX_TABLE_ROWS]
        notes = []
        if len(schedule) > MAX_TABLE_ROWS:
            notes.append(f"Showing the first {MAX_TABLE_ROWS} of {len(schedule)} instalments.")
        facts = [
            {"label": "EMI", "value": _money_cell(payload.get("emi"))},
            {
                "label": "Total interest",
                "value": _money_cell(payload.get("total_interest")),
            },
            {"label": "Total paid", "value": _money_cell(payload.get("total_paid"))},
            {"label": "Months", "value": _number_cell(payload.get("months"))},
            {"label": "Payoff", "value": _text_cell(payload.get("payoff_date"))},
        ]
        prepay = payload.get("prepay")
        if isinstance(prepay, dict):
            facts.extend(
                [
                    {"label": "New EMI", "value": _money_cell(prepay.get("new_emi"))},
                    {
                        "label": "Interest saved",
                        "value": _money_cell(prepay.get("interest_saved")),
                    },
                    {"label": "New months", "value": _number_cell(prepay.get("new_months"))},
                ]
            )
        snapshot = _snapshot(
            kind="amortize",
            title="Loan schedule",
            views=["Summary", "Table"],
            summary=_summary(facts),
            table=_table(
                caption="Monthly schedule",
                columns=[
                    {"key": "n", "label": "No.", "type": "number"},
                    {"key": "date", "label": "Date", "type": "date"},
                    {"key": "interest", "label": "Interest", "type": "money"},
                    {"key": "principal", "label": "Principal", "type": "money"},
                    {"key": "balance", "label": "Balance", "type": "money"},
                ],
                rows=[
                    [
                        _number_cell(row.get("n")),
                        _text_cell(row.get("date")),
                        _money_cell(row.get("interest_paise")),
                        _money_cell(row.get("principal_paise")),
                        _money_cell(row.get("balance_paise")),
                    ]
                    for row in shown
                ],
                total_rows=len(schedule),
                note=notes[-1] if notes else None,
            ),
            notes=notes,
            assumptions=[str(note) for note in payload.get("assumptions") or []],
            empty=not schedule,
        )
        return self.add(
            kind="amortize", title="Loan schedule", views=["Summary", "Table"], snapshot=snapshot
        )

    def _register_compound(
        self, payload: dict[str, Any], card: dict[str, Any]
    ) -> PresentationResult:
        del card
        yearly = payload.get("yearly") or []
        shown_yearly = yearly[:MAX_TABLE_ROWS]
        notes = []
        if len(yearly) > MAX_TABLE_ROWS:
            notes.append(f"Showing the first {MAX_TABLE_ROWS} of {len(yearly)} years.")
        snapshot = _snapshot(
            kind="compound",
            title="Growth at an assumed rate",
            views=["Summary", "Table"],
            summary=_summary(
                [
                    {"label": "Final", "value": _money_cell(payload.get("final"))},
                    {"label": "Contributed", "value": _money_cell(payload.get("contributed"))},
                    {"label": "Growth", "value": _money_cell(payload.get("growth"))},
                ]
            ),
            table=_table(
                caption="Balance by year",
                columns=[
                    {"key": "year", "label": "Year", "type": "number"},
                    {"key": "balance", "label": "Balance", "type": "money"},
                ],
                rows=[
                    [_number_cell(row.get("year")), _money_cell(row.get("balance_paise"))]
                    for row in shown_yearly
                ],
                total_rows=len(yearly),
                note=notes[-1] if notes else None,
            ),
            notes=notes,
            assumptions=[str(note) for note in payload.get("assumptions") or []],
            empty=not yearly,
        )
        return self.add(
            kind="compound",
            title="Growth at an assumed rate",
            views=["Summary", "Table"],
            snapshot=snapshot,
        )

    def _register_chart(self, payload: dict[str, Any], card: dict[str, Any]) -> PresentationResult:
        del payload
        title = str(card.get("title") or "Chart")
        snapshot = _snapshot(
            kind="chart",
            title=title,
            views=["Chart"],
            chart=copy.deepcopy(card),
            as_of=card.get("as_of"),
        )
        return self.add(kind="chart", title=title, views=["Chart"], snapshot=snapshot)


def _inherited_notes(sources: list[dict[str, Any]]) -> list[str]:
    notes = []
    preview_prefixes = (
        "Showing the first",
        "More rows are available",
        "The query returned more than",
    )
    for source in sources:
        for note in source.get("notes", []):
            if note.startswith(preview_prefixes):
                note = f"Source preview ({source['title']}): {note}"
            notes.append(note)
    return list(dict.fromkeys(notes))


def _source_coverage(sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    covered = [source["coverage"] for source in sources if source.get("coverage")]
    if not covered:
        return None
    return {
        "complete": all(source.get("coverage", {}).get("complete") is True for source in sources),
        "gaps": list(dict.fromkeys(gap for item in covered for gap in item.get("gaps", []))),
    }


def _column_type(name: str, values: list[Any]) -> str:
    if name.endswith("_paise"):
        return "money"
    numbers = [value for value in values if value is not None]
    if numbers and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) for value in numbers
    ):
        return "number"
    return "text"


def _cell_for_type(kind: str, value: Any) -> Cell:
    if kind == "money":
        return _money_cell(value)
    if kind == "number":
        return _number_cell(value)
    return _text_cell(value)


def _table_row(columns: list[TableColumn], row: list[Any]) -> list[Cell]:
    if len(row) != len(columns):
        raise PresentationError(
            f"Each table row needs {len(columns)} cells, one per column; this row has {len(row)}."
        )
    cells: list[Cell] = []
    for column, value in zip(columns, row, strict=True):
        if column.type == "money":
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise PresentationError(
                    f"Column {column.label!r} is money; its cells must be integer paise or null."
                )
            cells.append(_money_cell(value))
        elif column.type == "number":
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise PresentationError(
                    f"Column {column.label!r} is number; its cells must be numeric or null."
                )
            cells.append(_number_cell(value))
        elif column.type == "date":
            if value is not None and not isinstance(value, str):
                raise PresentationError(
                    f"Column {column.label!r} is a date; its cells must be ISO strings or null."
                )
            cells.append(_text_cell(value))
        else:
            if isinstance(value, (dict, list, tuple)):
                raise PresentationError(
                    f"Column {column.label!r} is text; its cells must be text, numbers, or null."
                )
            cells.append(_text_cell(value))
    return cells


def _fallback_card(result: PresentationResult) -> dict[str, Any]:
    """A readable result card for an ordinary read, before any composed answer."""
    return {
        "card": "result",
        "result_id": result.result_id,
        "snapshot": copy.deepcopy(result.snapshot),
    }


class TableColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    type: Literal["money", "number", "text", "date"] = "text"


class TableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    columns: list[TableColumn] = Field(min_length=1, max_length=MAX_TABLE_COLUMNS)
    rows: list[list[Any]] = Field(default_factory=list, max_length=MAX_TABLE_ROWS)
    source_result_ids: list[str] = Field(default_factory=list, max_length=MAX_PRESENT_NODES)
    as_of: str | None = None


class AnswerNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Stack", "Comparison", "Summary", "Table", "Chart", "Receipt"] = Field(
        alias="_type"
    )
    children: list[AnswerNode] | None = None
    result_id: str | None = Field(default=None, max_length=80)


def _validate_node(
    node: AnswerNode, registry: ResultRegistry, *, containers: int, count: list[int]
) -> None:
    count[0] += 1
    if count[0] > MAX_PRESENT_NODES:
        raise PresentationError(f"A composed answer has at most {MAX_PRESENT_NODES} nodes.")
    if node.type in CONTAINER_TYPES:
        containers += 1
        if containers > MAX_PRESENT_DEPTH:
            raise PresentationError(
                f"A composed answer nests at most {MAX_PRESENT_DEPTH} containers."
            )
        if node.result_id is not None:
            raise PresentationError(f"{node.type} is a container; put result_id on a leaf.")
        if not node.children:
            raise PresentationError(f"{node.type} needs at least one child.")
        if node.type == "Comparison":
            if len(node.children) < 2:
                raise PresentationError("Comparison needs at least two result sections.")
            if any(child.type not in LEAF_TYPES for child in node.children):
                raise PresentationError(
                    "Comparison aligns result sections; its children must be Summary, Table, "
                    "Chart, or Receipt."
                )
        for child in node.children:
            _validate_node(child, registry, containers=containers, count=count)
        return
    if node.children:
        raise PresentationError(f"{node.type} is a leaf and cannot have children.")
    if not node.result_id:
        raise PresentationError(f"{node.type} needs a result_id from ui_results.")
    result = registry.get(node.result_id)
    if node.type not in result.views:
        raise PresentationError(
            f"{node.type} is not available for {node.result_id} ({result.title}). "
            f"It offers {', '.join(result.views)}. Use a supported view, or read the data again."
        )


def _collect_ids(node: AnswerNode) -> list[str]:
    ids: list[str] = []
    if node.result_id:
        ids.append(node.result_id)
    for child in node.children or []:
        ids.extend(_collect_ids(child))
    return ids


def build_answer(
    registry: ResultRegistry, display: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a model-composed tree and build the self-contained answer card."""
    try:
        node = AnswerNode.model_validate(display)
    except ValidationError as exc:
        raise PresentationError(
            "The answer tree is not valid. Use _type with Stack, Comparison, Summary, Table, "
            "Chart, or Receipt, and only children or result_id."
        ) from exc
    _validate_node(node, registry, containers=0, count=[0])
    referenced: list[str] = []
    for result_id in _collect_ids(node):
        if result_id not in referenced:
            referenced.append(result_id)
    results = [registry.get(result_id).snapshot for result_id in referenced]
    card = {
        "card": "answer",
        "version": 1,
        "display": node.model_dump(by_alias=True, exclude_none=True),
        "results": results,
    }
    return card, {
        "shown": True,
        "sections": sum(1 for node_type in _node_types(node) if node_type in LEAF_TYPES),
        "results": [
            {"id": result_id, "title": registry.get(result_id).title} for result_id in referenced
        ],
    }


def _node_types(node: AnswerNode) -> list[str]:
    types = [node.type]
    for child in node.children or []:
        types.extend(_node_types(child))
    return types


__all__ = [
    "ANSWER_TYPES",
    "MAX_PRESENT_DEPTH",
    "MAX_PRESENT_NODES",
    "MAX_TABLE_COLUMNS",
    "MAX_TABLE_ROWS",
    "AnswerNode",
    "PresentationError",
    "PresentationResult",
    "Registration",
    "ResultRegistry",
    "TableColumn",
    "TableInput",
    "build_answer",
]
