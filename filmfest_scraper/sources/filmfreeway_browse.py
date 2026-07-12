"""Seeds from FilmFreeway's public browse/directory pages. FilmFreeway's
browse UI is JS-rendered, so filmfreeway.com is registered in
fetcher.JS_REQUIRED_DOMAINS and every fetch of it goes straight through
Playwright.

For each festival profile page found on the browse listing, also fetches
the profile page itself to pull the festival's official website link (if
present) -- this gives us a high-confidence filmfreeway_url directly, better
than the generic extractor's link-guessing on arbitrary festival sites.

FilmFreeway's exact markup may change over time; the link-matching here is
heuristic (URL-pattern + link-text based) rather than tied to specific CSS
classes, to be a bit more resilient to markup changes -- but this source may
still need occasional adjustment if FilmFreeway substantially redesigns.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

from bs4 import BeautifulSoup

from filmfest_scraper.config import CrawlConfig
from filmfest_scraper.models import normalize_domain
from filmfest_scraper.ratelimiter import PolitenessManager
from filmfest_scraper.sources.base import DirectorySource, DiscoveredFestival, FetchFn, register_source

logger = logging.getLogger(__name__)

PROFILE_LINK_RE = re.compile(r"^/[a-z0-9][a-z0-9-]{2,}/?$", re.IGNORECASE)

RESERVED_SLUGS = {
    "festivals", "login", "signup", "about", "pricing", "help", "blog",
    "contests", "screenplay-contests", "projects", "features",
}

WEBSITE_LINK_TEXT = ("official website", "visit website", "website")


BASE_URL = "https://filmfreeway.com/festivals"


@register_source
class FilmFreewayBrowseSource(DirectorySource):
    name = "filmfreeway_browse"
    seed_urls = [BASE_URL]

    def discover(
        self, fetch_url_fn: FetchFn, politeness: PolitenessManager, config: CrawlConfig
    ) -> Iterator[DiscoveredFestival]:
        # FilmFreeway's browse listing has ~200 pages (~12k festivals) --
        # walking pages beyond page 1 is what surfaces the smaller/regional
        # festivals that don't make it onto the front page.
        max_pages = max(1, config.filmfreeway_max_pages)
        seen_profiles: set[str] = set()

        for page_num in range(1, max_pages + 1):
            seed_url = BASE_URL if page_num == 1 else f"{BASE_URL}?page={page_num}"
            result = fetch_url_fn(seed_url, politeness, config)
            if not result.ok:
                logger.warning("Could not fetch FilmFreeway seed %s: %s", seed_url, result.error)
                continue

            soup = BeautifulSoup(result.html, "lxml")
            profile_urls = [u for u in self._extract_profile_urls(soup) if u not in seen_profiles]
            seen_profiles.update(profile_urls)
            logger.info("FilmFreeway browse found %d new candidate profile links on %s", len(profile_urls), seed_url)

            for profile_url in profile_urls:
                profile_result = fetch_url_fn(profile_url, politeness, config)
                if not profile_result.ok:
                    logger.warning("Could not fetch FilmFreeway profile %s: %s", profile_url, profile_result.error)
                    continue

                profile_soup = BeautifulSoup(profile_result.html, "lxml")
                name_hint = self._extract_name(profile_soup)
                website = self._extract_official_website(profile_soup)

                if not website:
                    # No outbound official site found -- still worth recording
                    # the FilmFreeway page itself as the "homepage" so the
                    # orchestrator can at least extract contact info from it.
                    website = profile_url

                yield DiscoveredFestival(
                    name_hint=name_hint,
                    homepage_url=website,
                    source_name=self.name,
                    source_url=seed_url,
                    filmfreeway_url=profile_url,
                )

    def _extract_profile_urls(self, soup: BeautifulSoup) -> list[str]:
        urls = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            path = href
            if href.startswith("http"):
                if normalize_domain(href) != "filmfreeway.com":
                    continue
                path = "/" + href.split("filmfreeway.com", 1)[1].lstrip("/")
            if not PROFILE_LINK_RE.match(path):
                continue
            slug = path.strip("/").split("/")[0].lower()
            if slug in RESERVED_SLUGS:
                continue
            absolute = f"https://filmfreeway.com/{slug}"
            if absolute in seen:
                continue
            seen.add(absolute)
            urls.append(absolute)
        return urls

    def _extract_name(self, soup: BeautifulSoup) -> str:
        title_tag = soup.find("h1")
        if title_tag:
            return title_tag.get_text(strip=True)
        if soup.title:
            return soup.title.get_text(strip=True).split("|")[0].strip()
        return ""

    def _extract_official_website(self, soup: BeautifulSoup) -> str:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href.startswith("http"):
                continue
            domain = normalize_domain(href)
            if not domain or domain == "filmfreeway.com":
                continue
            link_text = a.get_text(strip=True).lower()
            if any(kw in link_text for kw in WEBSITE_LINK_TEXT):
                return href
        return ""
