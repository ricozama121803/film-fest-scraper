"""Duplicate detection between festival records."""

from __future__ import annotations

from difflib import SequenceMatcher

from filmfest_scraper.models import Festival, normalize_domain, normalize_name

STOPWORDS = {"festival", "film", "films", "international", "the", "fest"}

FUZZY_RATIO_THRESHOLD = 0.9


def loose_name_key(name: str) -> str:
    """Fuzzy secondary signal only -- strips common festival-naming stopwords
    and sorts remaining words, so word-order/minor-wording differences don't
    block a match. Never used as the primary/exact dedupe key.
    """
    normalized = normalize_name(name)
    words = [w for w in normalized.split() if w not in STOPWORDS]
    return " ".join(sorted(words))


def dedupe_key(festival: Festival) -> tuple[str, str]:
    return normalize_name(festival.name), normalize_domain(festival.website)


def _fuzzy_name_match(a: Festival, b: Festival) -> bool:
    key_a = loose_name_key(a.name)
    key_b = loose_name_key(b.name)
    if not key_a or not key_b:
        return False
    ratio = SequenceMatcher(None, key_a, key_b).ratio()
    return ratio >= FUZZY_RATIO_THRESHOLD


def is_duplicate(existing: Festival, candidate: Festival) -> bool:
    domain_a = normalize_domain(existing.website)
    domain_b = normalize_domain(candidate.website)

    if domain_a and domain_b:
        if domain_a == domain_b:
            return True
        # Both have known, different domains -- fuzzy name match alone should
        # not force a merge (avoids collapsing distinct festivals with
        # generically similar names into one record).
        return False

    name_a = normalize_name(existing.name)
    name_b = normalize_name(candidate.name)
    if name_a and name_a == name_b:
        return True

    # One or both domains blank: fall back to fuzzy name matching.
    return _fuzzy_name_match(existing, candidate)
