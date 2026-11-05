"""Standard process logging.

Uvicorn configures its own loggers; this only gives the application's loggers a root handler
when one is not already installed. Docker captures stdout, and there is no log shipping.
"""

from __future__ import annotations

import logging


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO)


__all__ = ["configure_logging"]
