"""The dependency object every tool receives."""

from __future__ import annotations

from dataclasses import dataclass, field

from arno.agent.presentation import ResultRegistry


@dataclass
class AgentDeps:
    conversation_id: str
    message_id: str | None = None
    run_id: str | None = None
    # Set by the run's root span: the trace a saved reply links to, for Flag.
    trace_id: str | None = None
    # The authenticated principal for trace attribution. Opaque, stable, never derived from
    # financial data; absent on CLI and scheduled runs, which use the local-actor label.
    user_id: str | None = None
    # Evidence for a movement: photo and voice captures set their own kind and ref. The client
    # may name a voice attachment, but the server fixes the kind.
    capture_kind: str = "chat"
    capture_ref: str | None = None
    # Server-set: photo attachment IDs carried by this turn's user message. `saw_images` becomes
    # True when ImageAttachments hydrates any attached image onto a request (including follow-ups).
    image_attachments: tuple[str, ...] = ()
    saw_images: bool = False
    # Bounded result snapshots for this turn. IDs are never reused across turns.
    presentation: ResultRegistry = field(default_factory=ResultRegistry)
    # How many lasting writes the user approved in this turn, for the trace's tap rate.
    approvals: int = 0
    # How many approval cards this turn offered. Offered and approved read as two states.
    approvals_offered: int = 0
    # Web sources cited so far this turn, so `[n]` stays unique across searches.
    web_sources: int = 0


__all__ = ["AgentDeps"]
