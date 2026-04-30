"""Step 1b – Fetch metadata (title, authors, year, venue, abstract) from APIs.

Reads ``out/dois.json``, queries the Semantic Scholar Graph API for each DOI
(or title when DOI is unavailable) and writes one JSON file per paper to
``out/extracted/`` using the same schema as ``extract_text.py``.

Usage::

    python scripts/fetch_metadata.py [--overwrite] [--delay 1.0]

Options
-------
--overwrite   Re-fetch papers that already have a file in out/extracted/.
--delay N     Seconds to wait between API calls (default: 1.0).
--mailto ADDR Email to include in CrossRef User-Agent (optional).
"""

import argparse
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DOIS_PATH = Path("out/dois.json")
EXTRACTED_DIR = Path("out/extracted")

# Semantic Scholar fields we want
S2_FIELDS = "title,authors,year,abstract,venue,externalIds,publicationTypes"
S2_BASE = "https://api.semanticscholar.org/graph/v1"

# CrossRef fallback (works without API key)
CROSSREF_BASE = "https://api.crossref.org/works"

TIMEOUT = 15  # seconds per HTTP request


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get_json(url: str, *, mailto: str | None = None) -> dict | None:
    """Fetch *url* and return parsed JSON, or ``None`` on error."""
    headers = {"Accept": "application/json"}
    if mailto:
        headers["User-Agent"] = f"qse-review/1.0 (mailto:{mailto})"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.debug("HTTP %s for %s", exc.code, url)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Request error for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------

def _s2_by_doi(doi: str, mailto: str | None = None) -> dict | None:
    doi_enc = urllib.parse.quote(doi, safe="")
    url = f"{S2_BASE}/paper/DOI:{doi_enc}?fields={S2_FIELDS}"
    return _get_json(url, mailto=mailto)


def _s2_by_title(title: str, mailto: str | None = None) -> dict | None:
    params = urllib.parse.urlencode({"query": title, "limit": 1, "fields": S2_FIELDS})
    url = f"{S2_BASE}/paper/search?{params}"
    data = _get_json(url, mailto=mailto)
    if data and data.get("data"):
        return data["data"][0]
    return None


# ---------------------------------------------------------------------------
# CrossRef fallback (abstract not always present, but title/year/authors are)
# ---------------------------------------------------------------------------

def _crossref_by_doi(doi: str, mailto: str | None = None) -> dict | None:
    doi_enc = urllib.parse.quote(doi, safe="")
    url = f"{CROSSREF_BASE}/{doi_enc}"
    if mailto:
        url += f"?mailto={urllib.parse.quote(mailto)}"
    data = _get_json(url, mailto=mailto)
    if data and data.get("message"):
        return data["message"]
    return None


def _parse_crossref(msg: dict) -> dict:
    """Extract relevant fields from a CrossRef message dict."""
    def _names(items: list) -> list[str]:
        return [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in items
        ]

    authors = _names(msg.get("author") or [])
    year = None
    for date_key in ("published-print", "published-online", "issued"):
        parts = (msg.get(date_key) or {}).get("date-parts", [[]])[0]
        if parts:
            year = parts[0]
            break

    title_list = msg.get("title") or []
    title = title_list[0] if title_list else None

    container = msg.get("container-title") or []
    venue = container[0] if container else None

    abstract_raw = msg.get("abstract", "")
    # CrossRef abstracts often contain JATS XML tags — strip them
    abstract = re.sub(r"<[^>]+>", " ", abstract_raw).strip() if abstract_raw else None
    abstract = re.sub(r"\s+", " ", abstract) if abstract else None

    return {
        "title": title,
        "year": year,
        "authors": authors or None,
        "venue": venue,
        "abstract": abstract,
        "venue_type": msg.get("type"),
    }


# ---------------------------------------------------------------------------
# Build extracted record
# ---------------------------------------------------------------------------

def _build_record(paper: dict, s2: dict | None, cr: dict | None) -> dict:
    """Merge data from doi record + Semantic Scholar + CrossRef into extracted JSON."""

    # --- bibliographic -------------------------------------------------
    doi = paper.get("doi")

    # Title: prefer S2 > CrossRef > spreadsheet
    title = (s2 or {}).get("title") or (cr or {}).get("title") or paper.get("title")

    # Year
    year = (s2 or {}).get("year") or (cr or {}).get("year") or paper.get("year")

    # Authors
    s2_authors = [(a.get("name") or "") for a in (s2 or {}).get("authors") or []]
    authors = s2_authors or (cr or {}).get("authors") or None
    if not authors and paper.get("authors"):
        # Spreadsheet authors are often a single string
        authors = [a.strip() for a in re.split(r",(?!\s*Jr\.)", paper["authors"]) if a.strip()]

    # Venue
    venue = (s2 or {}).get("venue") or (cr or {}).get("venue") or paper.get("sheet")

    # venue_type
    venue_type = (cr or {}).get("venue_type") or None

    # Abstract: prefer S2 > CrossRef
    abstract = (s2 or {}).get("abstract") or (cr or {}).get("abstract") or None

    # --- text_for_classification -----------------------------------------
    if abstract and len(abstract) > 80:
        text_for_classification = abstract
    elif title:
        # Last resort: use title (very short, low confidence)
        text_for_classification = title
    else:
        text_for_classification = ""

    # --- stem (safe filename) -------------------------------------------
    # Use DOI with slashes replaced, or a slug from the title
    if doi:
        stem = re.sub(r"[^\w\-]", "_", doi)
    else:
        slug = re.sub(r"\W+", "_", (title or "unknown"))[:80]
        stem = slug

    filename = f"{stem}.pdf"  # virtual — no real PDF exists

    return {
        "filename": filename,
        "stem": stem,
        "pages_extracted": 0,
        "full_text": abstract or "",
        "abstract": abstract,
        "text_for_classification": text_for_classification,
        "bibliographic": {
            "doi": doi,
            "title": title,
            "year": year,
            "authors": authors,
            "venue": venue,
            "venue_type": venue_type,
            "source": "semantic_scholar" if s2 else ("crossref" if cr else "spreadsheet"),
        },
        "ocr_used": False,
        "error": None,
        # Extra provenance not in extract_text.py schema (harmless)
        "fetch_metadata": {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "s2_found": s2 is not None,
            "crossref_found": cr is not None,
            "sheet": paper.get("sheet"),
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch abstracts for QSE papers.")
    parser.add_argument("--overwrite", action="store_true",
                        help="Re-fetch papers that already have an extracted file.")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between API calls (default: 1.0).")
    parser.add_argument("--mailto", default=None,
                        help="Email for polite-pool header (CrossRef).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after processing at most N new papers (skips already-fetched ones).")
    args = parser.parse_args()

    if not DOIS_PATH.exists():
        logger.error("dois.json not found at %s — run resolve_dois.py first.", DOIS_PATH)
        return

    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)

    papers: list[dict] = json.loads(DOIS_PATH.read_text(encoding="utf-8"))
    logger.info("Loaded %d papers from %s.", len(papers), DOIS_PATH)

    skipped = success = no_abstract = errors = 0
    processed = 0  # counts papers actually fetched (not skipped)
    pending_papers: list[tuple[dict, Path]] = []

    for paper in papers:
        doi = paper.get("doi")
        title = paper.get("title", "")

        if doi:
            stem = re.sub(r"[^\w\-]", "_", doi)
        else:
            stem = re.sub(r"\W+", "_", title)[:80]

        out_path = EXTRACTED_DIR / f"{stem}.json"
        if out_path.exists() and not args.overwrite:
            skipped += 1
            continue
        pending_papers.append((paper, out_path))

    if args.limit is not None and len(pending_papers) > args.limit:
        pending_papers = pending_papers[: args.limit]

    logger.info(
        "Metadata fetch queue — to process: %d  skipped: %d",
        len(pending_papers),
        skipped,
    )

    for i, (paper, out_path) in enumerate(pending_papers, 1):
        doi = paper.get("doi")
        title = paper.get("title", "")
        logger.info("[%d/%d] %s", i, len(pending_papers), title[:80])

        # --- Semantic Scholar ---
        s2 = None
        if doi:
            s2 = _s2_by_doi(doi, args.mailto)
            time.sleep(args.delay)
        if s2 is None and title:
            logger.debug("  S2 DOI miss — trying title search")
            s2 = _s2_by_title(title, args.mailto)
            time.sleep(args.delay)

        # --- CrossRef fallback when abstract is missing ---
        cr_parsed = None
        if doi and (s2 is None or not (s2 or {}).get("abstract")):
            logger.debug("  fetching CrossRef for abstract/metadata")
            cr_raw = _crossref_by_doi(doi, args.mailto)
            if cr_raw:
                cr_parsed = _parse_crossref(cr_raw)
            time.sleep(args.delay)

        record = _build_record(paper, s2, cr_parsed)

        out_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if record["abstract"]:
            success += 1
        else:
            no_abstract += 1
            logger.warning("  no abstract found: %s", title[:60])
        processed += 1

    logger.info(
        "Metadata fetch complete — processed: %d  success: %d  no_abstract: %d  skipped: %d  errors: %d",
        processed,
        success,
        no_abstract,
        skipped,
        errors,
    )


if __name__ == "__main__":
    main()
