#!/usr/bin/env python3
"""Discovers film festivals from seed directories and maintains a CSV of
results, incrementally updating it on each run.

Usage:
    python crawl.py --max-new 20 --verbose
"""

from __future__ import annotations

import argparse
import logging
import sys

from filmfest_scraper import extractor
from filmfest_scraper.config import CrawlConfig, load_config
from filmfest_scraper.dedupe import NON_DEDUPE_DOMAINS
from filmfest_scraper.fetcher import close_browser, fetch_url
from filmfest_scraper.logging_config import setup_logging
from filmfest_scraper.models import Festival, normalize_domain, normalize_name
from filmfest_scraper.ratelimiter import PolitenessManager
from filmfest_scraper.sources import get_all_sources
from filmfest_scraper.sources.base import DiscoveredFestival
from filmfest_scraper.store import (
    load_csv,
    load_exclusions,
    run_merge_pipeline,
    select_existing_to_refresh,
    write_csv,
)

logger = logging.getLogger(__name__)

CONTACT_PAGE_KEYWORDS = ("about", "contact", "deadline")

# Aliases the extracted country field may take for a given filter value --
# extractor.extract_dates_and_location() sometimes yields "USA" (via its
# state-abbreviation heuristic) and sometimes "United States" (via the
# generic city-country pattern) for the same country.
COUNTRY_ALIASES = {
    "usa": {"usa", "united states", "united states of america", "us"},
}


def country_allowed(country: str, filters: list[str] | None, strict: bool = False) -> bool:
    """Country filtering is best-effort by default: extraction of a
    festival's country from free-form page text often comes back blank, so
    an unset/blank country is treated as "unknown, don't drop it" rather
    than excluded. Pass strict=True (--strict-country) to instead require a
    confidently-detected match, dropping undetected-country candidates too
    -- trades recall for precision when false positives matter more than
    missed festivals."""
    if not filters:
        return True
    if not country:
        return not strict
    normalized = country.strip().lower()
    for f in filters:
        f_norm = f.strip().lower()
        if normalized == f_norm or normalized in COUNTRY_ALIASES.get(f_norm, set()):
            return True
    return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--csv-path", dest="csv_path", help="Override CSV output path")
    parser.add_argument("--exclude-file", dest="exclude_file", help="Override dont_include.txt path")
    parser.add_argument("--max-new", dest="max_new_festivals", type=int, help="Max newly discovered festivals this run")
    parser.add_argument("--max-depth", dest="max_depth", type=int, help="Max crawl depth")
    parser.add_argument("--max-pages", dest="filmfreeway_max_pages", type=int, help="Max FilmFreeway browse pages to walk")
    parser.add_argument("--country", dest="country_filter", help="Comma-separated list of countries to keep newly discovered festivals from (e.g. USA); unset = no filtering")
    parser.add_argument("--strict-country", dest="strict_country", action="store_true", help="With --country, also drop candidates whose country couldn't be confidently detected (default: keep them)")
    parser.add_argument("--max-existing-refresh", dest="max_existing_to_refresh", type=int, help="Max existing rows to refresh this run")
    parser.add_argument("--refresh-existing", dest="refresh_existing", action="store_true", help="Force re-check ALL existing rows, ignoring the cap")
    parser.add_argument("--sources", dest="sources", help="Comma-separated list of source names to run (default: all)")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="Crawl and extract but don't write the CSV")
    parser.add_argument("--verbose", "-v", dest="verbose", action="store_true", help="Enable DEBUG logging on console")
    return parser.parse_args(argv)


def build_festival_from_site(
    homepage_url: str,
    name_hint: str,
    source_name: str,
    source_url: str,
    politeness: PolitenessManager,
    config: CrawlConfig,
    filmfreeway_hint: str = "",
) -> Festival | None:
    homepage_result = fetch_url(homepage_url, politeness, config)
    if not homepage_result.ok:
        return None

    festival = extractor.extract_festival_page_fields(homepage_result, name_hint=name_hint)
    festival.source = source_name
    festival.source_url = source_url
    if filmfreeway_hint and not festival.filmfreeway_url:
        festival.filmfreeway_url = filmfreeway_hint

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(homepage_result.html, "lxml")
    internal_links = extractor.find_internal_links(
        soup, festival.website, CONTACT_PAGE_KEYWORDS
    )[: config.max_internal_pages_per_site]

    for link in internal_links:
        page_result = fetch_url(link, politeness, config)
        if not page_result.ok:
            continue
        page_festival = extractor.extract_festival_page_fields(page_result, name_hint=name_hint)
        merge_partial_into(festival, page_festival)

    return festival


def merge_partial_into(target: Festival, source: Festival) -> None:
    """Fill-blanks-only merge used when combining homepage + internal-page
    extraction results for the same festival (not the CSV merge -- see
    store.merge_festival for that)."""
    from filmfest_scraper.models import LIST_FIELDS

    for field_name in (
        "contact_emails", "filmfreeway_url", "city", "state_province", "country",
        "dates", "submission_deadlines", "instagram", "facebook", "twitter",
        "youtube", "tiktok", "linkedin", "phone", "contact_form_url",
    ):
        target_value = getattr(target, field_name)
        source_value = getattr(source, field_name)
        if field_name in LIST_FIELDS:
            seen = {v.lower() for v in target_value}
            for v in source_value:
                if v.lower() not in seen:
                    target_value.append(v)
                    seen.add(v.lower())
        else:
            if not target_value and source_value:
                setattr(target, field_name, source_value)


def cheap_dedupe_candidates(
    candidates: list[DiscoveredFestival], existing: list[Festival]
) -> list[DiscoveredFestival]:
    """Drops candidates that obviously match an existing row or an
    already-seen candidate, by normalized name/domain, before spending any
    fetch budget on them."""
    existing_keys = {(normalize_name(f.name), normalize_domain(f.website)) for f in existing}
    existing_domains = {normalize_domain(f.website) for f in existing if f.website}

    seen_domains = set()
    seen_names = set()
    result = []
    for c in candidates:
        domain = normalize_domain(c.homepage_url)
        if domain in NON_DEDUPE_DOMAINS:
            domain = ""
        name = normalize_name(c.name_hint)

        if domain and domain in existing_domains:
            continue
        if (name, domain) in existing_keys:
            continue
        if domain and domain in seen_domains:
            continue
        if not domain and name in seen_names:
            continue

        if domain:
            seen_domains.add(domain)
        else:
            seen_names.add(name)
        result.append(c)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cli_overrides = {
        "csv_path": args.csv_path,
        "exclude_file": args.exclude_file,
        "max_new_festivals": args.max_new_festivals,
        "max_depth": args.max_depth,
        "filmfreeway_max_pages": args.filmfreeway_max_pages,
        "country_filter": [c.strip() for c in args.country_filter.split(",")] if args.country_filter else None,
        "strict_country": args.strict_country or None,
        "max_existing_to_refresh": args.max_existing_to_refresh,
        "refresh_existing": args.refresh_existing or None,
        "sources": args.sources.split(",") if args.sources else None,
        "dry_run": args.dry_run or None,
        "verbose": args.verbose or None,
    }
    config = load_config(args.config, cli_overrides)

    setup_logging(config.log_path, verbose=config.verbose)
    logger.info("Starting film festival crawl (max_new=%d, max_existing_refresh=%d, refresh_existing=%s)",
                config.max_new_festivals, config.max_existing_to_refresh, config.refresh_existing)

    politeness = PolitenessManager(config.request_delay_seconds)

    try:
        existing = load_csv(config.csv_path)
        exclusions = load_exclusions(config.exclude_file)

        all_sources = get_all_sources()
        if config.sources:
            selected = {name: src for name, src in all_sources.items() if name in config.sources}
            unknown = set(config.sources) - set(all_sources)
            if unknown:
                logger.warning("Unknown source names requested and ignored: %s", ", ".join(sorted(unknown)))
        else:
            selected = all_sources

        discovered_candidates: list[DiscoveredFestival] = []
        for source_name, source in selected.items():
            try:
                count_before = len(discovered_candidates)
                for candidate in source.discover(fetch_url, politeness, config):
                    discovered_candidates.append(candidate)
                logger.info("Source '%s' yielded %d candidates", source_name, len(discovered_candidates) - count_before)
            except Exception:
                logger.exception("Source '%s' raised an unexpected error, skipping remaining candidates from it", source_name)

        discovered_candidates = cheap_dedupe_candidates(discovered_candidates, existing)
        logger.info("%d candidates remain after cheap dedupe against existing CSV + each other", len(discovered_candidates))

        new_festivals: list[Festival] = []
        for candidate in discovered_candidates:
            if len(new_festivals) >= config.max_new_festivals:
                logger.info("Reached max_new_festivals (%d) during site crawling, stopping discovery", config.max_new_festivals)
                break
            try:
                festival = build_festival_from_site(
                    candidate.homepage_url,
                    candidate.name_hint,
                    candidate.source_name,
                    candidate.source_url,
                    politeness,
                    config,
                    filmfreeway_hint=candidate.filmfreeway_url,
                )
            except Exception:
                logger.exception("Unexpected error building festival record for %s", candidate.homepage_url)
                continue

            if festival is not None:
                if not country_allowed(festival.country, config.country_filter, config.strict_country):
                    logger.debug("Skipping %s: country %r not in filter", festival.website, festival.country)
                    continue
                new_festivals.append(festival)

        merged, stats = run_merge_pipeline(existing, new_festivals, exclusions, config.max_new_festivals)

        refreshed_count = 0
        if config.refresh_existing:
            to_refresh = [f for f in merged if f.website]
        else:
            to_refresh = select_existing_to_refresh(merged, config.max_existing_to_refresh)

        for festival in to_refresh:
            try:
                refresh_result = fetch_url(festival.website, politeness, config)
                if not refresh_result.ok:
                    continue
                refreshed = extractor.extract_festival_page_fields(refresh_result, name_hint=festival.name)
                from filmfest_scraper.store import merge_festival
                before = festival.last_updated
                merge_festival(festival, refreshed)
                if festival.last_updated != before:
                    refreshed_count += 1
            except Exception:
                logger.exception("Unexpected error refreshing existing festival %s", festival.website)

        if config.dry_run:
            logger.info("Dry run: skipping CSV write. Would have %d total festivals.", len(merged))
        else:
            write_csv(config.csv_path, merged)

        logger.info(
            "Crawl complete: %d new added, %d existing updated via discovery, %d existing refreshed, "
            "%d excluded, %d capped, %d total festivals",
            stats["new_added"], stats["existing_updated"], refreshed_count,
            stats["excluded"], stats["capped"], len(merged),
        )
        return 0
    except Exception:
        logger.exception("Crawl failed with an unhandled error")
        return 1
    finally:
        close_browser()


if __name__ == "__main__":
    sys.exit(main())
