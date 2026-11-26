"""Web search through Brave, for models without a built-in search tool."""

from __future__ import annotations

from typing import Any

import httpx

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


async def brave_search(
    query: str,
    *,
    api_key: str,
    count: int = 5,
    first_id: int = 1,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Return `[{id, title, url, snippet, published_at}]`; ids count up from `first_id`."""
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        response = await client.get(
            BRAVE_URL,
            params={"q": query, "count": count},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        )
    response.raise_for_status()
    results = response.json().get("web", {}).get("results", [])
    return [
        {
            "id": str(number),
            "title": item.get("title", ""),
            "url": item["url"],
            "snippet": item.get("description", ""),
            "published_at": item.get("page_age"),
        }
        for number, item in enumerate(results, start=first_id)
    ]


__all__ = ["brave_search"]
