"""The dependency object every tool receives."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentDeps:
    conversation_id: str
    message_id: str | None = None
    run_id: str | None = None
    # Server-set: the thread has read outside text, so lasting writes ask first.
    flagged: bool = False
    # The current user message, so a new name it contains isn't marked as outside text.
    user_texts: tuple[str, ...] = ()
    # Evidence for a movement: photo and voice captures set their own kind and ref. The client
    # may name a voice attachment, but the server fixes the kind.
    capture_kind: str = "chat"
    capture_ref: str | None = None
    # Charts and cards a tool produced inside this turn, for the post-run extractor.
    cards: list[dict[str, object]] = field(default_factory=list)
    # How many lasting writes the user approved in this turn, for the trace's tap rate.
    approvals: int = 0


__all__ = ["AgentDeps"]
