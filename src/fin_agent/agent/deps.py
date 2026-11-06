"""The dependency object every tool receives."""

from __future__ import annotations

from dataclasses import dataclass, field

from fin_agent.agent.presentation import ResultRegistry


@dataclass
class AgentDeps:
    conversation_id: str
    message_id: str | None = None
    run_id: str | None = None
    # The authenticated principal for trace attribution. Opaque, stable, never derived from
    # financial data; absent on CLI and scheduled runs, which use the local-actor label.
    user_id: str | None = None
    # Server-set: the thread has read outside text, so lasting writes ask first.
    flagged: bool = False
    # The current user message, so a new name it contains isn't marked as outside text.
    user_texts: tuple[str, ...] = ()
    # Evidence for a movement: photo and voice captures set their own kind and ref. The client
    # may name a voice attachment, but the server fixes the kind.
    capture_kind: str = "chat"
    capture_ref: str | None = None
    # Server-started statement imports: (import_id, status). The model sees that the
    # server already ran the one import path, so it must not call import_document again.
    statement_imports: tuple[tuple[str, str], ...] = ()
    # Charts and cards a tool produced inside this turn, for the post-run extractor.
    cards: list[dict[str, object]] = field(default_factory=list)
    # Bounded result snapshots for this turn. IDs are never reused across turns.
    presentation: ResultRegistry = field(default_factory=ResultRegistry)
    # How many lasting writes the user approved in this turn, for the trace's tap rate.
    approvals: int = 0
    # How many approval cards this turn offered. Offered and approved read as two states.
    approvals_offered: int = 0


__all__ = ["AgentDeps"]
