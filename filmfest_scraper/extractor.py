"""Heuristic field extraction from a festival page's HTML/text.

These are all best-effort. Festival websites have no common template, so
every function here is a heuristic, not an authoritative parser -- blank or
occasionally imprecise results are an accepted tradeoff against pulling in a
heavy NLP/NER dependency for a handful of structured fields.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from filmfest_scraper.fetcher import FetchResult
from filmfest_scraper.models import Festival, normalize_domain

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?1?[-.\s]?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}")

EMAIL_NOISE_DOMAINS = {
    "example.com",
    "sentry.io",
    "wixpress.com",
    "godaddy.com",
    "yourdomain.com",
    "domain.com",
}

SOCIAL_DOMAIN_PATTERNS = {
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com", "fb.com"),
    "twitter": ("twitter.com", "x.com"),
    "youtube": ("youtube.com", "youtu.be"),
    "tiktok": ("tiktok.com",),
    "linkedin": ("linkedin.com",),
}

FILMFREEWAY_DOMAIN = "filmfreeway.com"

DATE_KEYWORDS = ("dates", "festival dates", "runs", "takes place")
DEADLINE_KEYWORDS = ("deadline", "submissions close", "entry deadline", "closes")
CONTACT_KEYWORDS = ("contact",)

# Common Month DD-DD, YYYY / Month DD - Month DD, YYYY style patterns.
DATE_PATTERN = re.compile(
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?\s*(?:[-–]\s*"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)?"
    r"\s*\d{1,2}(?:st|nd|rd|th)?)?,?\s*\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}\s*[-–]\s*\d{1,2}/\d{1,2}/\d{2,4}",
    re.IGNORECASE,
)

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}

COUNTRIES = {
    "USA", "United States", "Canada", "United Kingdom", "UK", "France", "Germany",
    "Italy", "Spain", "Australia", "Ireland", "Netherlands", "Belgium", "Sweden",
    "Norway", "Denmark", "Japan", "South Korea", "India", "Brazil", "Mexico",
    "Argentina", "New Zealand", "Portugal", "Poland", "Austria", "Switzerland",
}

# City names are short -- bound the match length so this can't greedily
# swallow an entire sentence just because it happens to end near a state
# abbreviation or country name (e.g. "...counterparts from countries like
# France" should not be parsed as a city called "...countries like").
CITY_STATE_RE = re.compile(r"\b([A-Z][A-Za-z\.]{1,25}(?:[ -][A-Z][A-Za-z\.]{1,25}){0,2}),\s*([A-Z]{2})\b")
CITY_COUNTRY_RE = re.compile(
    r"\b([A-Z][A-Za-z\.]{1,25}(?:[ -][A-Z][A-Za-z\.]{1,25}){0,2}),\s*("
    + "|".join(re.escape(c) for c in COUNTRIES) + r")\b"
)


def extract_emails(soup: BeautifulSoup, text: str) -> list[str]:
    found: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            addr = href.split(":", 1)[1].split("?", 1)[0].strip()
            if addr:
                found.add(addr)

    for match in EMAIL_RE.findall(text):
        found.add(match)

    cleaned = []
    seen_lower = set()
    for email in found:
        domain = email.split("@")[-1].lower()
        if domain in EMAIL_NOISE_DOMAINS:
            continue
        key = email.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        cleaned.append(email)
    return sorted(cleaned)


def extract_social_links(soup: BeautifulSoup) -> dict[str, str]:
    results: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        domain = normalize_domain(href)
        if not domain:
            continue
        for platform, patterns in SOCIAL_DOMAIN_PATTERNS.items():
            if platform in results:
                continue
            if any(domain == p or domain.endswith("." + p) for p in patterns):
                results[platform] = href
    return results


def extract_filmfreeway_link(soup: BeautifulSoup) -> str:
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        domain = normalize_domain(href)
        if domain == FILMFREEWAY_DOMAIN or domain.endswith("." + FILMFREEWAY_DOMAIN):
            return href
    return ""


def find_contact_info(soup: BeautifulSoup, text: str) -> tuple[str, str]:
    phone = ""
    phone_match = PHONE_RE.search(text)
    if phone_match:
        phone = phone_match.group(0).strip()

    contact_form_url = ""
    for a in soup.find_all("a", href=True):
        link_text = a.get_text(strip=True).lower()
        href = a["href"].strip()
        if any(kw in link_text for kw in CONTACT_KEYWORDS) or any(
            kw in href.lower() for kw in CONTACT_KEYWORDS
        ):
            contact_form_url = href
            break

    return phone, contact_form_url


def _find_keyword_context_matches(text: str, keywords: tuple[str, ...], window: int = 120) -> list[str]:
    matches = []
    lowered = text.lower()
    for kw in keywords:
        idx = 0
        while True:
            pos = lowered.find(kw, idx)
            if pos == -1:
                break
            start = max(0, pos - window)
            end = min(len(text), pos + window)
            snippet = text[start:end]
            date_match = DATE_PATTERN.search(snippet)
            if date_match:
                matches.append(date_match.group(0).strip())
            idx = pos + len(kw)
    return matches


def extract_dates_and_location(text: str) -> dict:
    result = {"dates": "", "submission_deadlines": [], "city": "", "state_province": "", "country": ""}

    date_matches = _find_keyword_context_matches(text, DATE_KEYWORDS)
    if not date_matches:
        # Fall back to the first plausible date pattern anywhere in the text.
        generic = DATE_PATTERN.search(text)
        if generic:
            date_matches = [generic.group(0).strip()]
    if date_matches:
        result["dates"] = date_matches[0]

    deadline_matches = _find_keyword_context_matches(text, DEADLINE_KEYWORDS)
    seen = set()
    deduped_deadlines = []
    for d in deadline_matches:
        if d.lower() not in seen:
            seen.add(d.lower())
            deduped_deadlines.append(d)
    result["submission_deadlines"] = deduped_deadlines

    city_state = CITY_STATE_RE.search(text)
    if city_state and city_state.group(2) in US_STATES and city_state.group(1).strip() not in COUNTRIES:
        result["city"] = city_state.group(1).strip()
        result["state_province"] = city_state.group(2)
        result["country"] = "USA"
    else:
        # Guard against prose that lists several country names in a row
        # (e.g. "...counterparts from countries like France, Brazil,
        # Germany..."), where one country name incidentally looks like a
        # "city" immediately followed by another country name.
        for city_country in CITY_COUNTRY_RE.finditer(text):
            candidate_city = city_country.group(1).strip()
            if candidate_city in COUNTRIES:
                continue
            result["city"] = candidate_city
            result["country"] = city_country.group(2)
            break

    return result


def extract_festival_page_fields(fetch_result: FetchResult, name_hint: str = "") -> Festival:
    soup = BeautifulSoup(fetch_result.html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)

    emails = extract_emails(soup, text)
    social = extract_social_links(soup)
    filmfreeway_url = extract_filmfreeway_link(soup)
    phone, contact_form_url = find_contact_info(soup, text)
    date_location = extract_dates_and_location(text)

    festival = Festival(
        name=name_hint,
        website=fetch_result.final_url or fetch_result.url,
        contact_emails=emails,
        filmfreeway_url=filmfreeway_url,
        city=date_location["city"],
        state_province=date_location["state_province"],
        country=date_location["country"],
        dates=date_location["dates"],
        submission_deadlines=date_location["submission_deadlines"],
        instagram=social.get("instagram", ""),
        facebook=social.get("facebook", ""),
        twitter=social.get("twitter", ""),
        youtube=social.get("youtube", ""),
        tiktok=social.get("tiktok", ""),
        linkedin=social.get("linkedin", ""),
        phone=phone,
        contact_form_url=contact_form_url,
    )
    return festival


def find_internal_links(soup: BeautifulSoup, base_url: str, keywords: tuple[str, ...]) -> list[str]:
    """Same-domain links whose href or link text matches one of the keywords."""
    from urllib.parse import urljoin

    base_domain = normalize_domain(base_url)
    candidates = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        link_text = a.get_text(strip=True).lower()
        if not any(kw in href.lower() or kw in link_text for kw in keywords):
            continue
        absolute = urljoin(base_url, href)
        domain = normalize_domain(absolute)
        if domain != base_domain:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        candidates.append(absolute)
    return candidates
