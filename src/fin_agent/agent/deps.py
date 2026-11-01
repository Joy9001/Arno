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
    # Charts and cards a tool produced inside this turn, for the post-run extractor.
    cards: list[dict[str, object]] = field(default_factory=list)


__all__ = ["AgentDeps"]
