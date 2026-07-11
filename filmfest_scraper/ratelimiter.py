"""Per-domain politeness: rate limiting + duplicate-URL suppression.

Deliberately does NOT check robots.txt -- that's an intentional product
decision (not an oversight), so this module only handles delay + dedupe.
"""

from __future__ import annotations

import logging
import time

from filmfest_scraper.models import normalize_domain

logger = logging.getLogger(__name__)


def _normalize_url(url: str) -> str:
    # Strip fragment only; keep query/path since two distinct paths on the
    # same domain are genuinely different pages.
    return url.split("#", 1)[0].strip()


class PolitenessManager:
    def __init__(self, delay_seconds: float):
        self.delay_seconds = delay_seconds
        self._last_request_time: dict[str, float] = {}
        self._seen_urls: set[str] = set()

    def wait_and_mark(self, url: str) -> bool:
        """Returns False (no wait, caller should skip the fetch entirely) if
        this URL was already handled this run. Otherwise sleeps out the
        remaining per-domain delay, marks the URL/domain, and returns True.
        """
        norm_url = _normalize_url(url)
        if norm_url in self._seen_urls:
            logger.debug("Duplicate URL this run, skipping: %s", url)
            return False

        domain = normalize_domain(url)
        now = time.monotonic()
        last = self._last_request_time.get(domain)
        if last is not None:
            elapsed = now - last
            remaining = self.delay_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)

        self._seen_urls.add(norm_url)
        self._last_request_time[domain] = time.monotonic()
        return True
