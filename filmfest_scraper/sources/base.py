"""Registry pattern for directory sources.

To add a new source: create a new module in this package implementing
DirectorySource (see wikipedia_list.py or regional_directory.py for a
template), decorate the class with @register_source, and add one import
line for the new module in sources/__init__.py. No other files need to
change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Iterator

from filmfest_scraper.config import CrawlConfig
from filmfest_scraper.fetcher import FetchResult
from filmfest_scraper.ratelimiter import PolitenessManager

FetchFn = Callable[[str, PolitenessManager, CrawlConfig], FetchResult]


@dataclass
class DiscoveredFestival:
    name_hint: str
    homepage_url: str
    source_name: str
    source_url: str
    filmfreeway_url: str = ""


class DirectorySource(ABC):
    name: str
    seed_urls: list[str]

    @abstractmethod
    def discover(
        self, fetch_url_fn: FetchFn, politeness: PolitenessManager, config: CrawlConfig
    ) -> Iterator[DiscoveredFestival]:
        """Yield DiscoveredFestival candidates found on this source's seed
        page(s). Sources only find candidate homepage URLs -- they do not
        crawl into each festival's own site (the orchestrator does that).
        """
        raise NotImplementedError


_REGISTRY: dict[str, DirectorySource] = {}


def register_source(cls):
    instance = cls()
    _REGISTRY[instance.name] = instance
    return cls


def get_all_sources() -> dict[str, DirectorySource]:
    return dict(_REGISTRY)
