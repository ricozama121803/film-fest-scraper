# film-fest-scraper

Crawls the web for film festivals and maintains a single CSV database of them
(`data/festivals.csv`), adding newly discovered festivals and refreshing
details on existing ones each time it runs.

## What it does

Each run:

1. **Discovers candidates** from a set of pluggable sources
   (`filmfest_scraper/sources/`):
   - `wikipedia_list` — Wikipedia's list of film festivals
   - `filmfreeway_browse` — FilmFreeway's festival browse pages
   - `regional_directory` — a regional festival directory
2. **Dedupes cheaply** — drops any candidate that already matches an existing
   row (by normalized name/domain) or a candidate already seen this run,
   before spending any fetch budget on it.
3. **Fetches and extracts** each remaining candidate's homepage, plus a few
   internal pages (about/contact/deadline), pulling out fields like contact
   emails, socials, dates, submission deadlines, etc. Fetching uses plain
   `requests` first and transparently falls back to a headless Playwright
   (Chromium) browser for JS-rendered sites (e.g. FilmFreeway pages).
4. **Merges results into the CSV**, skipping anything listed in
   `dont_include.txt`, up to a cap on how many new festivals to add per run.
5. **Refreshes a batch of existing rows** by re-fetching their sites and
   filling in/updating any fields that have changed.
6. **Writes the CSV** (unless `--dry-run`) and logs a summary to
   `logs/crawl.log`.

Requests are politeness-throttled (delay between requests, one fetch per URL
per run) and identify themselves with a descriptive `User-Agent` including a
contact email — see `config.yaml`.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Usage

```bash
source venv/bin/activate
python crawl.py
```

Common flags (all optional — defaults come from `config.yaml`):

| Flag | Effect |
|---|---|
| `--max-new N` | Max newly discovered festivals to add this run |
| `--max-depth N` | Max crawl depth |
| `--max-existing-refresh N` | Max existing rows to refresh this run |
| `--refresh-existing` | Force re-check **all** existing rows, ignoring the cap |
| `--sources a,b,c` | Only run these sources (default: all) |
| `--config path.yaml` | Use a different config file |
| `--csv-path path.csv` | Override the CSV output path |
| `--exclude-file path.txt` | Override the exclusions file path |
| `--dry-run` | Crawl and extract, but don't write the CSV |
| `--verbose` / `-v` | Enable DEBUG logging on the console |

Examples:

```bash
# Small test run without touching the CSV
python crawl.py --max-new 3 --dry-run --verbose

# Normal run using config.yaml defaults
python crawl.py

# Re-check every existing festival's site for updated info
python crawl.py --refresh-existing
```

## Files

- `config.yaml` — default crawl limits, timeouts, and paths.
- `dont_include.txt` — one festival name or website domain per line to
  permanently exclude (lines starting with `#` are comments).
- `data/festivals.csv` — the output database. Columns: name, website, city,
  state_province, country, venue, dates, submission_deadlines,
  filmfreeway_url, contact_emails, phone, contact_form_url, instagram,
  facebook, twitter, youtube, tiktok, linkedin, source, source_url,
  first_seen, last_updated, notes.
- `logs/crawl.log` — full run log (rotates per config); console shows INFO
  by default, DEBUG with `--verbose`.

## Adding a new source

Create a module in `filmfest_scraper/sources/` implementing the
`DirectorySource` interface (see `sources/base.py` and
`sources/regional_directory.py` for the template), then import it in
`filmfest_scraper/sources/__init__.py` so it gets registered.
