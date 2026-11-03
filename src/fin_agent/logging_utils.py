"""Keep amounts, merchant detail, and message text out of logs.

Logs carry IDs, timings, and error types. A DB error or a stray formatted message can still carry a
row value, so this filter redacts money, the `DETAIL` line of a Postgres error, and the exception
text that would otherwise be rendered verbatim.
"""

from __future__ import annotations

import logging
import re

AMOUNT = re.compile(r"(?:₹|Rs\.?|INR)\s?[\d,]+(?:\.\d+)?(?:\s*(?:lakh|crore|k))?", re.IGNORECASE)
PG_DETAIL = re.compile(r"\bDETAIL:\s.*", re.IGNORECASE)
PG_KEY = re.compile(r"\bKey \([^)]*\)=\([^)]*\)")
PG_CONTEXT = re.compile(r"\bCONTEXT:\s.*", re.IGNORECASE)
EDGE = re.compile(r"^(?P<prefix>.*?)(?P<detail>DETAIL:|CONTEXT:)")


def sanitize(text: str) -> str:
    text = AMOUNT.sub("[amount]", text)
    text = PG_KEY.sub("Key ([redacted])", text)
    text = EDGE.sub(r"\g<prefix>\g<detail> [redacted]", text, count=1)
    text = PG_DETAIL.sub("DETAIL: [redacted]", text)
    text = PG_CONTEXT.sub("CONTEXT: [redacted]", text)
    return text


class ContentlessLogFilter(logging.Filter):
    """Sanitize the message, its arguments, and any traceback before a handler writes it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: sanitize(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(
                    sanitize(value) if isinstance(value, str) else value for value in record.args
                )
        if record.exc_info:
            formatter = logging.Formatter()
            record.exc_text = sanitize(formatter.formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = sanitize(record.exc_text)
        return True


def install() -> None:
    """Add the filter to the root handlers used by the API and the CLI."""
    log_filter = ContentlessLogFilter()
    for handler in logging.getLogger().handlers:
        if not any(isinstance(existing, ContentlessLogFilter) for existing in handler.filters):
            handler.addFilter(log_filter)
