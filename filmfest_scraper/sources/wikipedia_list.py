"""Seeds from Wikipedia's "List of film festivals" pages.

Wikipedia list-article bodies are full of external links that are NOT
festival homepages (news-story citations, YouTube channels, review sites,
etc. cited as references). Those are too noisy to use directly. Instead,
this source follows the *internal* wiki link for each festival mentioned in
the list to that festival's own Wikipedia article, then pulls the "Official
website" link out of that article's infobox -- infobox website links are a
high-confidence source of real official festival sites. Festivals without a
Wikipedia article, or whose article has no infobox website, are simply
skipped by this source (they may still be picked up by other sources).
"""

from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from filmfest_scraper.config import CrawlConfig
from filmfest_scraper.models import normalize_domain
from filmfest_scraper.ratelimiter import PolitenessManager
from filmfest_scraper.sources.base import DirectorySource, DiscoveredFestival, FetchFn, register_source

logger = logging.getLogger(__name__)

NON_ARTICLE_PREFIXES = (
    "Special:", "Help:", "Wikipedia:", "Portal:", "Category:", "Template:",
    "File:", "Talk:", "User:", "Draft:", "Module:", "MediaWiki:",
)

NON_FESTIVAL_DOMAINS = {
    "wikipedia.org",
    "wikimedia.org",
    "wikidata.org",
    "archive.org",
    "doi.org",
}

# Bound how many festival Wikipedia articles this source will fetch/check
# per run, so a huge list page can't blow the crawl's time/request budget
# regardless of the orchestrator's later max_new_festivals cap.
CANDIDATE_MULTIPLIER = 2
MIN_CANDIDATE_CAP = 20


def _is_article_link(absolute_url: str) -> bool:
    """absolute_url must already be resolved (Wikipedia serves protocol-
    relative hrefs like "//en.wikipedia.org/wiki/X", not just "/wiki/X")."""
    if normalize_domain(absolute_url) != "en.wikipedia.org":
        return False
    path = absolute_url.split("en.wikipedia.org", 1)[1]
    if not path.startswith("/wiki/"):
        return False
    title = path[len("/wiki/"):].split("#", 1)[0]
    if not title:
        return False
    if any(title.startswith(prefix) for prefix in NON_ARTICLE_PREFIXES):
        return False
    if title.lower().startswith("list_of"):
        return False
    return True


FESTIVAL_TITLE_KEYWORDS = ("festival", "biennale", "filmfest")


def _article_title(soup: BeautifulSoup) -> str:
    heading = soup.find(id="firstHeading")
    if heading:
        return heading.get_text(strip=True)
    if soup.title:
        return soup.title.get_text(strip=True).split("-")[0].strip()
    return ""


def _is_festival_article(soup: BeautifulSoup) -> bool:
    """List pages link to plenty of non-festival articles too (host cities,
    countries, sponsoring organizations, etc.) -- those articles usually
    still have *an* infobox website (e.g. a city's official government
    site), so the infobox check alone isn't a strong enough filter. Gate on
    the article's own title actually looking like a festival."""
    title = _article_title(soup).lower()
    return any(kw in title for kw in FESTIVAL_TITLE_KEYWORDS)


def _find_infobox_website(soup: BeautifulSoup, base_url: str) -> str:
    infobox = soup.find("table", class_=re.compile(r"\binfobox\b"))
    if not infobox:
        return ""

    for row in infobox.find_all("tr"):
        header = row.find("th")
        if not header:
            continue
        header_text = header.get_text(strip=True).lower()
        if "website" not in header_text:
            continue
        link = row.find("a", class_=re.compile(r"\bexternal\b"), href=True)
        if not link:
            link = row.find("a", href=True)
        if link:
            return urljoin(base_url, link["href"].strip())

    # Fallback: any external link directly inside the infobox, in case the
    # "Website" row uses a template layout without a plain <th>.
    ext_link = infobox.find("a", class_=re.compile(r"\bexternal\b"), href=True)
    if ext_link:
        domain = normalize_domain(ext_link["href"])
        if domain and domain not in NON_FESTIVAL_DOMAINS:
            return urljoin(base_url, ext_link["href"].strip())

    return ""


@register_source
class WikipediaListSource(DirectorySource):
    name = "wikipedia_list"
    seed_urls = [
        "https://en.wikipedia.org/wiki/List_of_film_festivals",
        "https://en.wikipedia.org/wiki/List_of_film_festivals_in_North_America",
        "https://en.wikipedia.org/wiki/List_of_film_festivals_in_Europe",
        "https://en.wikipedia.org/wiki/List_of_film_festivals_in_Asia",
    ]

    def discover(
        self, fetch_url_fn: FetchFn, politeness: PolitenessManager, config: CrawlConfig
    ) -> Iterator[DiscoveredFestival]:
        candidate_cap = max(MIN_CANDIDATE_CAP, config.max_new_festivals * CANDIDATE_MULTIPLIER)
        yielded = 0

        for seed_url in self.seed_urls:
            if yielded >= candidate_cap:
                break

            result = fetch_url_fn(seed_url, politeness, config)
            if not result.ok:
                logger.warning("Could not fetch Wikipedia seed %s: %s", seed_url, result.error)
                continue

            soup = BeautifulSoup(result.html, "lxml")
            content = soup.find(id="mw-content-text") or soup

            article_links: dict[str, str] = {}
            for a in content.find_all("a", href=True):
                href = a["href"].strip()
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                absolute = urljoin(seed_url, href)
                if not _is_article_link(absolute):
                    continue
                name_hint = a.get_text(strip=True)
                if not name_hint or len(name_hint) < 2:
                    continue
                article_links.setdefault(absolute, name_hint)

            logger.info(
                "Wikipedia source found %d candidate festival articles on %s",
                len(article_links), seed_url,
            )

            checked = 0
            found = 0
            for article_url, name_hint in article_links.items():
                if yielded >= candidate_cap:
                    break
                checked += 1

                article_result = fetch_url_fn(article_url, politeness, config)
                if not article_result.ok:
                    continue

                article_soup = BeautifulSoup(article_result.html, "lxml")
                if not _is_festival_article(article_soup):
                    continue

                website = _find_infobox_website(article_soup, article_url)
                if not website:
                    continue
                domain = normalize_domain(website)
                if not domain or domain in NON_FESTIVAL_DOMAINS:
                    continue

                found += 1
                yielded += 1
                yield DiscoveredFestival(
                    name_hint=_article_title(article_soup) or name_hint,
                    homepage_url=website,
                    source_name=self.name,
                    source_url=seed_url,
                )

            logger.info(
                "Wikipedia source checked %d article(s), found %d with an infobox website (from %s)",
                checked, found, seed_url,
            )
