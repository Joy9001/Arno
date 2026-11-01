"""The Indian default categories.

`reducible` marks the categories the affordability engine may use for a "cut a category"
tradeoff. Committed costs (rent, EMIs, insurance, family transfers, savings) are not reducible.
Categories are labels on spending, never ledger accounts.
"""

from __future__ import annotations

from typing import Final

# (name, reducible)
DEFAULT_CATEGORIES: Final[tuple[tuple[str, bool], ...]] = (
    ("Food & Dining", True),
    ("Groceries", True),
    ("Transport", True),
    ("Fuel", True),
    ("Shopping", True),
    ("Entertainment", True),
    ("Subscriptions", True),
    ("Utilities", False),
    ("Mobile & Internet", False),
    ("Rent", False),
    ("Home Maintenance", False),
    ("Health", False),
    ("Education", False),
    ("Insurance", False),
    ("Loan Interest", False),
    ("Family Transfer", False),
    ("Savings & Investments", False),
    ("Fees & Charges", True),
    ("Travel", True),
    ("Gifts & Donations", True),
    ("Personal Care", True),
    ("Other", True),
)

DEFAULT_CATEGORY_NAMES: Final[frozenset[str]] = frozenset(name for name, _ in DEFAULT_CATEGORIES)
