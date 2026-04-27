"""Browser-like HTTP headers for Congress.gov (api + www) and bioguide.congress.gov.

Congress.gov may return 403 for obvious bot User-Agents. ``Accept-Encoding`` is
left unset so httpx can apply its default negotiation (gzip/deflate) without
requiring optional brotli support.
"""

from __future__ import annotations

CONGRESS_GOV_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json, text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
