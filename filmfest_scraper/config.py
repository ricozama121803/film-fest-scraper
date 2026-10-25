"""Configuration loading: config.yaml defaults, overridable by CLI flags."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CrawlConfig:
    max_new_festivals: int = 75
    max_depth: int = 2
    max_internal_pages_per_site: int = 4
    max_existing_to_refresh: int = 20
    request_delay_seconds: float = 1.5
    request_timeout_seconds: int = 15
    max_retries: int = 2
    filmfreeway_max_pages: int = 5
    country_filter: list[str] | None = None
    strict_country: bool = False

    csv_path: str = "data/festivals.csv"
    exclude_file: str = "dont_include.txt"
    log_path: str = "logs/crawl.log"

    user_agent: str = "FilmFestScraperBot/0.1"
    playwright_headless: bool = True

    refresh_existing: bool = False
    sources: list[str] | None = None
    dry_run: bool = False
    verbose: bool = False


_YAML_KEY_MAP = {
    # yaml section -> attribute names within it map 1:1 to CrawlConfig fields
    "crawl": [
        "max_new_festivals",
        "max_depth",
        "max_internal_pages_per_site",
        "max_existing_to_refresh",
        "request_delay_seconds",
        "request_timeout_seconds",
        "max_retries",
        "filmfreeway_max_pages",
        "country_filter",
        "strict_country",
    ],
    "paths": {
        "csv_path": "csv_path",
        "exclude_file": "exclude_file",
        "log_path": "log_path",
    },
    "fetcher": {
        "user_agent": "user_agent",
        "playwright_headless": "playwright_headless",
    },
}


def _load_yaml(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(path: str = "config.yaml", cli_overrides: dict[str, Any] | None = None) -> CrawlConfig:
    """Build a CrawlConfig from dataclass defaults, then config.yaml, then CLI overrides."""
    config = CrawlConfig()
    yaml_data = _load_yaml(path)

    crawl_section = yaml_data.get("crawl", {}) or {}
    for key in _YAML_KEY_MAP["crawl"]:
        if key in crawl_section:
            setattr(config, key, crawl_section[key])

    paths_section = yaml_data.get("paths", {}) or {}
    for yaml_key, attr in _YAML_KEY_MAP["paths"].items():
        if yaml_key in paths_section:
            setattr(config, attr, paths_section[yaml_key])

    fetcher_section = yaml_data.get("fetcher", {}) or {}
    for yaml_key, attr in _YAML_KEY_MAP["fetcher"].items():
        if yaml_key in fetcher_section:
            setattr(config, attr, fetcher_section[yaml_key])

    valid_fields = {f.name for f in fields(CrawlConfig)}
    for key, value in (cli_overrides or {}).items():
        if value is None:
            continue
        if key not in valid_fields:
            continue
        setattr(config, key, value)

    return config
