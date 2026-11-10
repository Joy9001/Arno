"""Tested money maths and the projection engine, in whole paise.

The agent calls these from Code Mode instead of writing the formulas itself. They are pure,
deterministic, and use `Decimal` for money; rates are the only floating-point values and every
result is rounded back to whole paise at the boundary.
"""

from arno.engine.affordability import Hypothetical, afford
from arno.engine.money import amortize, compound
from arno.engine.projection import project

__all__ = ["Hypothetical", "afford", "amortize", "compound", "project"]
