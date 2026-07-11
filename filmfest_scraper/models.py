"""Core data model for a discovered festival, plus the normalization helpers
that both dedupe.py and store.py rely on as their single source of truth."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# Column order used everywhere a Festival is serialized to/from CSV.
CSV_FIELDS = [
    "name",
    "website",
    "city",
    "state_province",
    "country",
    "venue",
    "dates",
    "submission_deadlines",
    "filmfreeway_url",
    "contact_emails",
    "phone",
    "contact_form_url",
    "instagram",
    "facebook",
    "twitter",
    "youtube",
    "tiktok",
    "linkedin",
    "source",
    "source_url",
    "first_seen",
    "last_updated",
    "notes",
]

# Fields that hold multiple values, joined/split with "; " in the CSV.
LIST_FIELDS = {"contact_emails", "submission_deadlines"}


@dataclass
class Festival:
    name: str = ""
    website: str = ""
    contact_emails: list[str] = field(default_factory=list)
    filmfreeway_url: str = ""
    city: str = ""
    state_province: str = ""
    country: str = ""
    venue: str = ""
    dates: str = ""
    submission_deadlines: list[str] = field(default_factory=list)
    instagram: str = ""
    facebook: str = ""
    twitter: str = ""
    youtube: str = ""
    tiktok: str = ""
    linkedin: str = ""
    phone: str = ""
    contact_form_url: str = ""
    source: str = ""
    source_url: str = ""
    first_seen: str = ""
    last_updated: str = ""
    notes: str = ""

    def to_row(self) -> dict[str, str]:
        row = {}
        for name in CSV_FIELDS:
            value = getattr(self, name)
            if name in LIST_FIELDS:
                row[name] = "; ".join(value)
            else:
                row[name] = value
        return row

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Festival":
        kwargs = {}
        for name in CSV_FIELDS:
            raw = (row.get(name) or "").strip()
            if name in LIST_FIELDS:
                kwargs[name] = [v.strip() for v in raw.split(";") if v.strip()] if raw else []
            else:
                kwargs[name] = raw
        return cls(**kwargs)


def normalize_domain(url: str) -> str:
    """Lowercase host with scheme/www./path/query/port stripped.

    Returns "" if the URL has no discernible host (blank input, garbage input).
    """
    if not url:
        return ""
    url = url.strip()
    if "://" not in url:
        url = "//" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[len("www."):]
    return host


def normalize_name(name: str) -> str:
    """Lowercase, strip, collapse whitespace, strip trailing punctuation.

    Deliberately does NOT strip generic words like "festival"/"film" here —
    that would risk over-merging distinct festivals that happen to share a
    generic word. See dedupe.loose_name_key for the fuzzy secondary signal.
    """
    if not name:
        return ""
    name = name.strip().lower()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"[^\w\s&'-]", "", name)
    return name.strip()
