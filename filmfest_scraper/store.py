"""CSV persistence, exclusion-list handling, and the incremental merge
pipeline that combines newly discovered festivals with what's already saved.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from filmfest_scraper.dedupe import is_duplicate
from filmfest_scraper.models import CSV_FIELDS, LIST_FIELDS, Festival, normalize_domain, normalize_name

logger = logging.getLogger(__name__)


@dataclass
class ExclusionSet:
    excluded_domains: set[str]
    excluded_names: set[str]

    def matches(self, festival: Festival) -> bool:
        domain = normalize_domain(festival.website)
        if domain and domain in self.excluded_domains:
            return True
        name = normalize_name(festival.name)
        if name and name in self.excluded_names:
            return True
        return False


def _looks_like_domain(line: str) -> bool:
    return "." in line and " " not in line


def load_exclusions(path: str) -> ExclusionSet:
    excluded_domains: set[str] = set()
    excluded_names: set[str] = set()

    p = Path(path)
    if not p.exists():
        logger.info("No exclusion file at %s, skipping", path)
        return ExclusionSet(excluded_domains, excluded_names)

    with p.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if _looks_like_domain(line):
                excluded_domains.add(normalize_domain(line))
            else:
                excluded_names.add(normalize_name(line))

    logger.info(
        "Loaded %d excluded domains, %d excluded names from %s",
        len(excluded_domains), len(excluded_names), path,
    )
    return ExclusionSet(excluded_domains, excluded_names)


def load_csv(path: str) -> list[Festival]:
    p = Path(path)
    if not p.exists():
        logger.info("No existing CSV at %s, starting fresh", path)
        return []

    festivals = []
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            festivals.append(Festival.from_row(row))
    logger.info("Loaded %d existing festivals from %s", len(festivals), path)
    return festivals


def write_csv(path: str, festivals: list[Festival]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    ordered = sorted(
        festivals,
        key=lambda f: (f.country.lower(), f.state_province.lower(), f.city.lower(), f.name.lower()),
    )

    tmp_path = p.with_suffix(p.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for festival in ordered:
            writer.writerow(festival.to_row())

    os.replace(tmp_path, p)
    logger.info("Wrote %d festivals to %s", len(ordered), path)


def merge_festival(existing: Festival, incoming: Festival) -> Festival:
    changed = False
    for field_name in CSV_FIELDS:
        if field_name in ("first_seen", "last_updated"):
            continue
        existing_value = getattr(existing, field_name)
        incoming_value = getattr(incoming, field_name)

        if field_name in LIST_FIELDS:
            merged = list(existing_value)
            seen_lower = {v.lower() for v in merged}
            for v in incoming_value:
                if v.lower() not in seen_lower:
                    merged.append(v)
                    seen_lower.add(v.lower())
                    changed = True
            setattr(existing, field_name, merged)
        else:
            if not existing_value and incoming_value:
                setattr(existing, field_name, incoming_value)
                changed = True

    if changed:
        existing.last_updated = date.today().isoformat()
    return existing


def run_merge_pipeline(
    existing_list: list[Festival],
    discovered_list: list[Festival],
    exclusions: ExclusionSet,
    max_new_festivals: int,
) -> tuple[list[Festival], dict[str, int]]:
    """Merges newly discovered festivals into the existing list, respecting
    exclusions and the max_new_festivals cap. Returns (merged_list, stats).
    """
    stats = {"new_added": 0, "existing_updated": 0, "excluded": 0, "capped": 0}

    working = [f for f in existing_list if not exclusions.matches(f)]
    excluded_existing_count = len(existing_list) - len(working)
    stats["excluded"] += excluded_existing_count

    today = date.today().isoformat()

    for candidate in discovered_list:
        if exclusions.matches(candidate):
            stats["excluded"] += 1
            continue

        match = None
        for existing in working:
            if is_duplicate(existing, candidate):
                match = existing
                break

        if match is not None:
            merge_festival(match, candidate)
            stats["existing_updated"] += 1
            continue

        if stats["new_added"] >= max_new_festivals:
            stats["capped"] += 1
            continue

        if not candidate.first_seen:
            candidate.first_seen = today
        if not candidate.last_updated:
            candidate.last_updated = today
        working.append(candidate)
        stats["new_added"] += 1

    if stats["capped"]:
        logger.info(
            "Hit max_new_festivals cap (%d); %d additional discovered festivals were not added this run",
            max_new_festivals, stats["capped"],
        )

    return working, stats


def select_existing_to_refresh(existing_list: list[Festival], limit: int) -> list[Festival]:
    """Picks up to `limit` existing rows with a website but the most blank
    key fields, prioritizing the most incomplete rows first.
    """
    key_fields = ("contact_emails", "instagram", "facebook", "twitter", "filmfreeway_url")

    def incompleteness(f: Festival) -> int:
        score = 0
        for field_name in key_fields:
            value = getattr(f, field_name)
            if not value:
                score += 1
        return score

    candidates = [f for f in existing_list if f.website and incompleteness(f) > 0]
    candidates.sort(key=incompleteness, reverse=True)
    return candidates[:limit]
