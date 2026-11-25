"""Arno's compaction wiring, tested through the capability list it actually builds.

The generic harness strategies are the library's job to test. What Arno decides is that a
long thread falls back from a summary to a sliding window, and that the summary itself uses
the backup model so it still works with the primary down.
"""

from __future__ import annotations

from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.compaction import (
    FallbackCompaction,
    SlidingWindowCompaction,
    SummarizingCompaction,
    TieredCompaction,
)

from arno.agent.capabilities import build_capabilities


def _tiered(backup=None) -> TieredCompaction:
    capabilities = build_capabilities(primary=TestModel(), backup=backup, context_window=1_000)
    return next(item for item in capabilities if isinstance(item, TieredCompaction))


def test_the_summary_falls_back_to_a_sliding_window():
    tiered = _tiered()
    fallback = next(item for item in tiered.tiers if isinstance(item, FallbackCompaction))
    assert isinstance(fallback.fallback_chain[0], SummarizingCompaction)
    assert isinstance(fallback.fallback_chain[1], SlidingWindowCompaction)


def test_the_summary_uses_the_backup_when_the_main_model_is_down():
    tiered = _tiered(backup=TestModel())
    fallback = next(item for item in tiered.tiers if isinstance(item, FallbackCompaction))
    summarizer = fallback.fallback_chain[0]
    assert isinstance(summarizer, SummarizingCompaction)
    assert isinstance(summarizer.model, FallbackModel)
