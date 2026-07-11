"""Importing this package registers all built-in directory sources.

Add new sources by creating a module implementing DirectorySource (see
base.py / regional_directory.py for the template) and importing it here.
"""

from filmfest_scraper.sources import (  # noqa: F401
    filmfreeway_browse,
    regional_directory,
    wikipedia_list,
)
from filmfest_scraper.sources.base import get_all_sources  # noqa: F401
