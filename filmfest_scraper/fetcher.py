"""Page fetching: requests+BeautifulSoup by default, Playwright fallback for
JS-rendered pages. Callers only ever see a FetchResult -- they never need to
know which mechanism actually fetched the page.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal, Optional

import requests
from bs4 import BeautifulSoup

from filmfest_scraper.config import CrawlConfig
from filmfest_scraper.models import normalize_domain
from filmfest_scraper.ratelimiter import PolitenessManager

logger = logging.getLogger(__name__)

# Domains known to render their content via JS (e.g. React/Next SPAs) --
# route straight to Playwright and skip wasting a plain-requests attempt.
JS_REQUIRED_DOMAINS = {
    "filmfreeway.com",
}

_JS_SHELL_MARKERS = (
    'id="root"',
    "id='root'",
    'id="app"',
    "id='app'",
    "__next_data__",
    "data-reactroot",
    "ng-app",
)

FetchMethod = Literal["requests", "playwright", "failed"]


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: Optional[int]
    html: str
    fetch_method: FetchMethod
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.fetch_method != "failed"


# Lazily-created, run-lifetime Playwright browser singleton.
_playwright_ctx = None
_browser = None

# Run-lifetime cache of fetch results by normalized URL. A source may yield
# a candidate homepage_url that it already fetched itself (e.g. falling back
# to a directory profile page as the "homepage" when no external site was
# found) -- without this, the second fetch_url() call for that same URL
# would be silently dropped by PolitenessManager's duplicate-URL guard,
# which exists to prevent redundant network fetches, not to fail callers
# needing the same result twice.
_fetch_cache: dict[str, FetchResult] = {}


def _get_browser(config: CrawlConfig):
    global _playwright_ctx, _browser
    if _browser is not None:
        return _browser
    from playwright.sync_api import sync_playwright

    _playwright_ctx = sync_playwright().start()
    _browser = _playwright_ctx.chromium.launch(headless=config.playwright_headless)
    logger.info("Launched Playwright chromium browser (headless=%s)", config.playwright_headless)
    return _browser


def close_browser() -> None:
    global _playwright_ctx, _browser
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            logger.debug("Error closing Playwright browser", exc_info=True)
        _browser = None
    if _playwright_ctx is not None:
        try:
            _playwright_ctx.stop()
        except Exception:
            logger.debug("Error stopping Playwright context", exc_info=True)
        _playwright_ctx = None
    _fetch_cache.clear()


def needs_js_rendering(html: str, url: str) -> bool:
    domain = normalize_domain(url)
    if domain in JS_REQUIRED_DOMAINS:
        return True

    lowered = html.lower()
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    visible_text = soup.get_text(strip=True)

    if len(visible_text) >= 200:
        return False

    if any(marker in lowered for marker in _JS_SHELL_MARKERS):
        return True

    noscript = soup.find("noscript")
    if noscript and "enable javascript" in noscript.get_text(strip=True).lower():
        return True

    # Short visible text but no clear JS-shell marker -- likely just a thin
    # static page, not worth the Playwright cost.
    return False


def _fetch_with_requests(url: str, config: CrawlConfig) -> FetchResult:
    headers = {"User-Agent": config.user_agent}
    last_error = None
    for attempt in range(config.max_retries + 1):
        try:
            resp = requests.get(
                url, headers=headers, timeout=config.request_timeout_seconds
            )
            if resp.status_code >= 400:
                return FetchResult(
                    url=url,
                    final_url=resp.url,
                    status_code=resp.status_code,
                    html="",
                    fetch_method="failed",
                    error=f"http_status_{resp.status_code}",
                )
            return FetchResult(
                url=url,
                final_url=resp.url,
                status_code=resp.status_code,
                html=resp.text,
                fetch_method="requests",
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = str(exc)
            if attempt < config.max_retries:
                time.sleep(1 + attempt)
        except requests.RequestException as exc:
            last_error = str(exc)
            break

    return FetchResult(
        url=url, final_url=url, status_code=None, html="", fetch_method="failed", error=last_error
    )


def _fetch_with_playwright(url: str, config: CrawlConfig) -> FetchResult:
    try:
        browser = _get_browser(config)
        page = browser.new_page(user_agent=config.user_agent)
        try:
            response = page.goto(
                url, wait_until="networkidle", timeout=config.request_timeout_seconds * 1000
            )
            html = page.content()
            status_code = response.status if response else None
            final_url = page.url
        finally:
            page.close()

        if status_code is not None and status_code >= 400:
            return FetchResult(
                url=url,
                final_url=final_url,
                status_code=status_code,
                html="",
                fetch_method="failed",
                error=f"http_status_{status_code}",
            )
        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=status_code,
            html=html,
            fetch_method="playwright",
        )
    except Exception as exc:
        logger.debug("Playwright fetch failed for %s", url, exc_info=True)
        return FetchResult(
            url=url, final_url=url, status_code=None, html="", fetch_method="failed", error=str(exc)
        )


def fetch_url(url: str, politeness: PolitenessManager, config: CrawlConfig) -> FetchResult:
    norm_url = url.split("#", 1)[0].strip()
    cached = _fetch_cache.get(norm_url)
    if cached is not None:
        return cached

    if not politeness.wait_and_mark(url):
        return FetchResult(
            url=url, final_url=url, status_code=None, html="", fetch_method="failed",
            error="duplicate_url",
        )

    domain = normalize_domain(url)
    if domain in JS_REQUIRED_DOMAINS:
        result = _fetch_with_playwright(url, config)
    else:
        result = _fetch_with_requests(url, config)
        if result.ok and needs_js_rendering(result.html, url):
            logger.debug("Falling back to Playwright for %s", url)
            result = _fetch_with_playwright(url, config)

    if not result.ok:
        logger.warning("SKIP %s reason=%s", url, result.error)
    _fetch_cache[norm_url] = result
    return result
