"""Template source: parses a simple HTML listing page's external links.

This is the copy-paste starting point for adding a new directory source --
most static "here's a list of festivals" pages (film commissions, regional
arts councils, aggregator blogs) can be scraped with roughly this shape:
fetch the seed page(s), grab external links inside the main content area,
use link text as the name hint.

To add a new source of this kind:
1. Copy this file to sources/my_new_source.py.
2. Rename the class, set `name` to a unique registry key, and set
   `seed_urls` to the page(s) you want to crawl.
3. Adjust `content_selector` / `excluded_domains` if the page structure
   needs it.
4. Add `from filmfest_scraper.sources import my_new_source` to
   sources/__init__.py.
"""

from __future__ import annotations

import logging
from typing import Iterator

from bs4 import BeautifulSoup

from filmfest_scraper.config import CrawlConfig
from filmfest_scraper.models import normalize_domain
from filmfest_scraper.ratelimiter import PolitenessManager
from filmfest_scraper.sources.base import DirectorySource, DiscoveredFestival, FetchFn, register_source

logger = logging.getLogger(__name__)


@register_source
class FilmFestivalsOrgDirectorySource(DirectorySource):
    """FilmFestivals.com's public directory of festivals -- a static,
    non-JS listing page, used here as the reference example for the
    "generic HTML listing page" source template.
    """

    name = "regional_directory"
    seed_urls = [
        "https://filmfestivals.com/en/festivals",
    ]
    excluded_domains = {"filmfestivals.com", "facebook.com", "twitter.com", "x.com", "instagram.com"}
    content_selector = None  # None = whole page; set to a CSS selector to narrow scope

    def discover(
        self, fetch_url_fn: FetchFn, politeness: PolitenessManager, config: CrawlConfig
    ) -> Iterator[DiscoveredFestival]:
        for seed_url in self.seed_urls:
            result = fetch_url_fn(seed_url, politeness, config)
            if not result.ok:
                logger.warning("Could not fetch %s: %s", seed_url, result.error)
                continue

            soup = BeautifulSoup(result.html, "lxml")
            content = soup.select_one(self.content_selector) if self.content_selector else soup

            seen = set()
            count = 0
            for a in content.find_all("a", href=True):
                href = a["href"].strip()
                if not href.startswith("http"):
                    continue
                domain = normalize_domain(href)
                if not domain or domain in self.excluded_domains:
                    continue
                if href in seen:
                    continue
                seen.add(href)

                name_hint = a.get_text(strip=True)
                if not name_hint or len(name_hint) < 2:
                    continue

                count += 1
                yield DiscoveredFestival(
                    name_hint=name_hint,
                    homepage_url=href,
                    source_name=self.name,
                    source_url=seed_url,
                )

            logger.info("%s found %d candidate links on %s", self.name, count, seed_url)
