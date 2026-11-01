"""Tested money maths: amortization and compounding, in whole paise.

The agent calls these from Code Mode instead of writing the formulas itself. They are pure,
deterministic, and use `Decimal` for money; rates are the only floating-point values and every
result is rounded back to whole paise at the boundary.
"""

from fin_agent.engine.money import amortize, compound

__all__ = ["amortize", "compound"]
