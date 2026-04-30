"""Step 0 – Extract papers from the spreadsheet and resolve DOIs.

Reads ``papers/QSE - Papers.xlsx`` (or another path via ``--input``),
extracts all paper entries (year, authors, title, source URL / DOI),
and resolves a DOI for every entry that does not already have one via:

  1. Regex extraction from the cell hyperlink URL (doi.org, dl.acm.org/doi/…).
  2. CrossRef title-search API (fallback, with respectful rate limiting).

Writes the result to ``out/dois.json`` (path overridable via ``--output``).
The file is **gitignored** — it lives in ``out/`` which is local-only.

This JSON is the canonical input for the next pipeline step
(``fetch_abstracts.py``) and replaces the old ``papers/*.pdf`` workflow
when working from a spreadsheet.

Output format (``out/dois.json``):
  A JSON array of objects, one per paper:
  {
    "sheet":    "<conference/journal name>",
    "year":     <int>,
    "authors":  "<author string>",
    "title":    "<paper title>",
    "doi":      "<DOI string or null>",
    "url":      "<fallback URL or null>",
    "doi_source": "spreadsheet" | "url_regex" | "crossref" | null
  }

Rate limiting:
  - CrossRef is called only when no DOI can be extracted from the URL.
  - Default delay between CrossRef calls: 1 s (--crossref-delay to adjust).
  - CrossRef ``score`` threshold: 30.0 (--min-score to adjust).
    Results below the threshold are discarded to avoid false matches.
"""

import argparse
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import openpyxl  # type: ignore[import]
except ImportError:
    openpyxl = None  # type: ignore[assignment]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_INPUT = Path("papers/QSE - Papers.xlsx")
DEFAULT_OUTPUT = Path("out/dois.json")
CROSSREF_TIMEOUT = 15  # seconds
DEFAULT_CROSSREF_DELAY = 1.0  # seconds between CrossRef calls
DEFAULT_MIN_SCORE = 30.0

# DOI regex: captures 10.XXXX/... from plain text or URLs
_DOI_RE = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/|https?://dl\.acm\.org/doi/)"
    r"(10\.\d{4,9}/[^\s,;\"'<>\[\]{}()]+)",
    re.IGNORECASE,
)

# arXiv URL → no DOI, keep URL as-is
_ARXIV_RE = re.compile(r"arxiv\.org/abs/(\d{4}\.\d{4,5})", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Spreadsheet parsing
# ---------------------------------------------------------------------------

def _parse_title_cell(cell) -> tuple[str | None, str | None, str | None]:
    """Return (doi, url, title) from a spreadsheet cell.

    Three formats are handled:
    - ``=HYPERLINK("url", "title")`` formula (stored as literal string)
    - External hyperlink object (``cell.hyperlink.target``)
    - Plain text title with no link
    """
    val = cell.value
    hl = cell.hyperlink

    # --- Formula style: =HYPERLINK("url", "title") -----------------------
    if isinstance(val, str) and val.strip().upper().startswith("=HYPERLINK"):
        m = re.match(r'=HYPERLINK\("([^"]+)",\s*"([^"]*)"\)', val.strip(), re.IGNORECASE)
        if m:
            link_url, title = m.group(1), m.group(2)
            doi = _doi_from_url(link_url)
            return doi, (None if doi else link_url), title
        # malformed formula — treat whole value as title
        return None, None, val

    # --- External hyperlink object ----------------------------------------
    title = val if isinstance(val, str) else None
    if hl and hl.target:
        doi = _doi_from_url(hl.target)
        return doi, (None if doi else hl.target), title

    return None, None, title


def _doi_from_url(url: str) -> str | None:
    """Extract and normalise a DOI from a URL, or return None."""
    m = _DOI_RE.search(url)
    if m:
        return m.group(1).rstrip(".")
    return None


def _parse_spreadsheet(path: Path) -> list[dict]:
    """Return a list of raw paper dicts from all sheets."""
    if openpyxl is None:
        raise ImportError(
            "openpyxl is required to read the spreadsheet.\n"
            "Install it with: pip install openpyxl"
        )

    wb = openpyxl.load_workbook(path)
    papers: list[dict] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
            # Expect columns: YEAR | AUTHORS | TITLE  (possibly offset by 1 header row)
            if len(row) < 3:
                continue
            year_cell, authors_cell, title_cell = row[0], row[1], row[2]

            year = year_cell.value
            if not isinstance(year, (int, float)):
                continue  # header or empty row
            year = int(year)

            authors = authors_cell.value
            if not isinstance(authors, str) or not authors.strip():
                continue

            doi, url, title = _parse_title_cell(title_cell)

            if not title or title.strip().upper() == "TITLE":
                continue

            papers.append(
                {
                    "sheet": sheet_name,
                    "year": year,
                    "authors": authors.strip(),
                    "title": title.strip(),
                    "doi": doi,
                    "url": url,
                    "doi_source": "spreadsheet" if doi else None,
                }
            )

    logger.info("Parsed %d papers from %d sheets.", len(papers), len(wb.sheetnames))
    return papers


# ---------------------------------------------------------------------------
# CrossRef title-search fallback
# ---------------------------------------------------------------------------

def _crossref_resolve(title: str, min_score: float, mailto: str | None) -> str | None:
    """Query CrossRef by title and return a DOI if confidence is high enough."""
    query = urllib.parse.quote(title[:200])
    url = f"https://api.crossref.org/works?query.title={query}&rows=1&select=DOI,title,score"

    mailto_str = mailto or "qse-review@local"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"qse-review/1.0 (mailto:{mailto_str})"},
    )
    try:
        with urllib.request.urlopen(req, timeout=CROSSREF_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("CrossRef request failed for %r: %s", title[:60], exc)
        return None

    items = data.get("message", {}).get("items", [])
    if not items:
        return None

    item = items[0]
    score = item.get("score", 0.0)
    if score < min_score:
        logger.debug("  CrossRef score %.1f below threshold for %r", score, title[:60])
        return None

    doi = item.get("DOI")
    if doi:
        logger.debug("  CrossRef resolved DOI %s (score=%.1f)", doi, score)
    return doi


# ---------------------------------------------------------------------------
# Main resolution loop
# ---------------------------------------------------------------------------

def resolve_dois(
    papers: list[dict],
    crossref_delay: float,
    min_score: float,
    mailto: str | None,
    skip_crossref: bool,
) -> list[dict]:
    """Resolve missing DOIs in-place; return the updated list."""
    needs_crossref = [p for p in papers if p["doi"] is None]
    has_doi = len(papers) - len(needs_crossref)

    logger.info(
        "%d papers already have a DOI; %d need CrossRef resolution.",
        has_doi,
        len(needs_crossref),
    )

    if skip_crossref or not needs_crossref:
        return papers

    logger.info(
        "Starting CrossRef resolution with %.1fs delay between calls…",
        crossref_delay,
    )

    for i, paper in enumerate(needs_crossref, 1):
        logger.info(
            "  [%d/%d] %s", i, len(needs_crossref), paper["title"][:70]
        )
        doi = _crossref_resolve(paper["title"], min_score, mailto)
        if doi:
            paper["doi"] = doi
            paper["doi_source"] = "crossref"
        else:
            logger.warning("    → no DOI resolved; URL kept: %s", paper.get("url"))

        if i < len(needs_crossref):
            time.sleep(crossref_delay)

    resolved = sum(1 for p in needs_crossref if p["doi"])
    unresolved = len(needs_crossref) - resolved
    logger.info(
        "CrossRef done — resolved: %d  still missing: %d", resolved, unresolved
    )
    return papers


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract papers from the QSE spreadsheet and resolve DOIs."
    )
    p.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        metavar="XLSX",
        help=f"Path to the input spreadsheet (default: {DEFAULT_INPUT}).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        metavar="JSON",
        help=f"Path for the output JSON file (default: {DEFAULT_OUTPUT}; gitignored).",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    p.add_argument(
        "--skip-crossref",
        action="store_true",
        help="Do not call CrossRef; only use DOIs already present in the spreadsheet.",
    )
    p.add_argument(
        "--crossref-delay",
        type=float,
        default=DEFAULT_CROSSREF_DELAY,
        metavar="SECONDS",
        help=f"Seconds to wait between CrossRef API calls (default: {DEFAULT_CROSSREF_DELAY}).",
    )
    p.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        metavar="SCORE",
        help=(
            f"Minimum CrossRef relevance score to accept a DOI match "
            f"(default: {DEFAULT_MIN_SCORE})."
        ),
    )
    p.add_argument(
        "--mailto",
        type=str,
        default=None,
        metavar="EMAIL",
        help=(
            "E-mail address for the CrossRef Polite Pool "
            "(higher rate limits). Optional but recommended."
        ),
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()

    if not args.input.exists():
        logger.error("Spreadsheet not found: %s", args.input)
        raise SystemExit(1)

    if args.output.exists() and not args.overwrite:
        logger.info(
            "Output already exists: %s  (use --overwrite to regenerate)", args.output
        )
        raise SystemExit(0)

    papers = _parse_spreadsheet(args.input)

    papers = resolve_dois(
        papers,
        crossref_delay=args.crossref_delay,
        min_score=args.min_score,
        mailto=args.mailto,
        skip_crossref=args.skip_crossref,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(papers, fh, ensure_ascii=False, indent=2)

    total = len(papers)
    with_doi = sum(1 for p in papers if p["doi"])
    logger.info(
        "Wrote %d papers to %s  (with DOI: %d / %d)",
        total,
        args.output,
        with_doi,
        total,
    )


if __name__ == "__main__":
    main()
