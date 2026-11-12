
"""DuckDuckGo search adapter (no LangChain)."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=128)
def _cached_search(query: str, max_results: int):

    from ddgs import DDGS

    try:
        # Use the same configuration as the successful standalone test.
        rows = DDGS().text(
            query,
            max_results=max_results
        )

        results = []

        for item in rows or []:
            title = str(item.get("title") or "").strip()
            url = str(
                item.get("href") or item.get("url") or ""
            ).strip()
            snippet = str(
                item.get("body") or item.get("snippet") or ""
            ).strip()

            if title and url:
                results.append({
                    "title": title[:300],
                    "url": url,
                    "snippet": snippet[:1200]
                })

        return results

    except Exception as exc:
        print(f"DuckDuckGo search unavailable: {exc}")
        return []


def search_web(
    query: str,
    max_results: int = 5
) -> list[dict[str, str]]:

    query = " ".join((query or "").split())

    if not query:
        return []

    max_results = max(1, min(int(max_results), 8))

    try:
        return list(_cached_search(query, max_results))
    except Exception as exc:
        print(f"Web search failed: {exc}")
        return []
