"""Step 1 – Extract text from PDF papers (deterministic, no LLM).

For each PDF found in ``papers/``, this script:
  1. Reads up to ``MAX_PAGES`` pages with *pdfplumber*.
  2. Falls back to OCR (pytesseract + pdf2image) if text layer is empty/minimal
     and ``--ocr`` is requested or tesseract is auto-detected.
  3. Tries to isolate the **abstract** using common section-header patterns.
  4. Attempts best-effort extraction of bibliographic metadata:
     a. Extracts DOI from the first page (very reliable).
     b. If a DOI is found, queries the Crossref API for precise metadata
        (title, authors, year, venue, venue_type).
     c. Falls back to regex heuristics when no DOI is found or the API fails.
  5. Writes a JSON file to ``out/extracted/`` that downstream scripts use.

The JSON payload per paper:
  - ``filename``              – original PDF file name
  - ``pages_extracted``       – number of pages actually read
  - ``full_text``             – concatenated page text
  - ``abstract``              – detected abstract section (or ``null``)
  - ``text_for_classification`` – abstract if found; else first
                                  ``MAX_WORDS_FOR_CLASSIFICATION`` words
  - ``bibliographic``         – metadata dict:
      - ``doi``               – DOI string (or ``null``)
      - ``title``             – paper title (or ``null``)
      - ``year``              – publication year as int (or ``null``)
      - ``authors``           – list of author name strings (or ``null``)
      - ``venue``             – journal / conference name (or ``null``)
      - ``venue_type``        – Crossref type string, e.g. "journal-article",
                                "proceedings-article" (or ``null``)
      - ``source``            – "crossref" | "heuristic"
  - ``ocr_used``              – True when OCR fallback was triggered
  - ``error``                 – error message if extraction failed
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pdfplumber

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration defaults (can be overridden via CLI flags)
# ---------------------------------------------------------------------------
PAPERS_DIR = Path("papers")
EXTRACTED_DIR = Path("out/extracted")
MAX_PAGES = 10
MAX_WORDS_FOR_CLASSIFICATION = 1500
MIN_ABSTRACT_LENGTH = 100
MIN_TEXT_FOR_OCR = 200
CROSSREF_TIMEOUT = 8  # seconds

_EMPTY_BIBLIOGRAPHIC: dict = {
    "doi": None,
    "title": None,
    "year": None,
    "authors": None,
    "venue": None,
    "venue_type": None,
    "source": "heuristic",
}


# ---------------------------------------------------------------------------
# Abstract extraction
# ---------------------------------------------------------------------------

def _extract_abstract(text: str) -> str | None:
    """Return the abstract section from *text*, or ``None`` if not found."""
    patterns = [
        # IEEE inline: "Abstract—" or "Abstract–" (em-dash / en-dash)
        r"(?i)\babstract\s*[—–]\s*(.*?)(?=\n\s*(?:keywords|index\s+terms|i+\.\s+introduction|1[\.\s]+introduction)|\Z)",
        # Standard header followed by body text until double-newline or known section
        r"(?i)\babstract\b[\s:—–\-]*\n+(.*?)(?=\n\s*\n|\n\s*(?:keywords|index\s+terms|introduction))",
        # "Abstract:" inline style
        r"(?i)\babstract:\s+(.*?)(?=\n\s*\n|\n\s*(?:keywords|index\s+terms|introduction))",
        # Springer / generic period style: "Abstract. Body text…"
        r"(?i)\babstract\.\s+(.*?)(?=\n\s*\n|\n\s*(?:keywords|introduction))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            abstract = re.sub(r"\s+", " ", match.group(1)).strip()
            if len(abstract) > MIN_ABSTRACT_LENGTH:
                return abstract
    return None


# ---------------------------------------------------------------------------
# DOI extraction
# ---------------------------------------------------------------------------

# Matches DOIs in plain text, URLs (doi.org/..., dx.doi.org/...) and
# labelled forms (DOI:, doi:).  Trailing punctuation is stripped.
_DOI_RE = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*|DOI:\s*)?(10\.\d{4,9}/[^\s,;\"\'<>\[\]{}()]+)",
    re.IGNORECASE,
)


def _extract_doi(text: str) -> str | None:
    """Return the first DOI found in *text*, cleaned of trailing punctuation."""
    match = _DOI_RE.search(text)
    if not match:
        return None
    doi = match.group(1).rstrip(".")
    return doi


# ---------------------------------------------------------------------------
# Crossref API
# ---------------------------------------------------------------------------

def _fetch_crossref(doi: str, mailto: str | None = None) -> dict | None:
    """Query the Crossref REST API for *doi*.

    Returns a partial bibliographic dict on success, ``None`` on any failure.
    Pass *mailto* to join the polite pool (higher rate limits).
    """
    encoded = urllib.parse.quote(doi, safe="")
    url = f"https://api.crossref.org/works/{encoded}"
    if mailto:
        url += f"?mailto={urllib.parse.quote(mailto)}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"qse-review/1.0 (mailto:{mailto or 'unknown'})"},
    )
    try:
        with urllib.request.urlopen(req, timeout=CROSSREF_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.debug("Crossref lookup failed for %s: %s", doi, exc)
        return None

    msg = data.get("message", {})

    titles = msg.get("title") or []
    title = titles[0] if titles else None

    authors_raw = msg.get("author") or []
    authors: list[str] | None = [
        " ".join(filter(None, [a.get("given"), a.get("family")])).strip()
        for a in authors_raw
        if a.get("family")
    ] or None

    date_parts = (msg.get("published") or msg.get("published-print") or {}).get(
        "date-parts", [[]]
    )
    year: int | None = date_parts[0][0] if date_parts and date_parts[0] else None

    venues = msg.get("container-title") or []
    venue = venues[0] if venues else None

    venue_type = msg.get("type")  # e.g. "journal-article", "proceedings-article"

    return {
        "title": title,
        "year": year,
        "authors": authors,
        "venue": venue,
        "venue_type": venue_type,
    }


# ---------------------------------------------------------------------------
# Heuristic fallback
# ---------------------------------------------------------------------------

_VENUE_SKIP_RE = re.compile(
    r"(?i)(proceedings|transactions|conference|workshop|journal|symposium"
    r"|arxiv|preprint|doi:|http|@|\bvol\b|\bno\b|\bpp\b|\bpages\b)"
)

_NAME_RE = re.compile(
    r"^([A-Z][a-záéíóúàèìòùâêîôûãõç\-\.]+(?:\s+[A-Z][a-záéíóúàèìòùâêîôûãõç\-\.]+){1,3}"
    r"(?:\s*,\s*[A-Z][a-záéíóúàèìòùâêîôûãõç\-\.]+(?:\s+[A-Z][a-záéíóúàèìòùâêîôûãõç\-\.]+){1,3})*"
    r"(?:\s+and\s+[A-Z][a-záéíóúàèìòùâêîôûãõç\-\.]+(?:\s+[A-Z][a-záéíóúàèìòùâêîôûãõç\-\.]+){1,3})?)$"
)

_VENUE_LINE_RE = re.compile(
    r"(?i)(proceedings\s+of\b|(?:ieee|acm|springer)\s+\w|"
    r"(?:transactions|journal|letters)\s+on\b|"
    r"(?:conference|workshop|symposium)\s+on\b|"
    r"arXiv:\d{4}\.\d{4,5})"
)


def _heuristic_bibliographic(first_page: str) -> dict:
    """Extract title, year, authors, venue via regex heuristics."""
    result: dict = {
        "doi": None,
        "title": None,
        "year": None,
        "authors": None,
        "venue": None,
        "venue_type": None,
        "source": "heuristic",
    }

    # Year
    for pat in [
        r"©\s*((?:19|20)\d{2})\b",
        r"[Cc]opyright\s+(?:©\s*)?((?:19|20)\d{2})\b",
        r"\b((?:19|20)\d{2})\s+IEEE\b",
        r"\bIEEE\s+((?:19|20)\d{2})\b",
        r"\bPublished\s+(?:in\s+)?((?:19|20)\d{2})\b",
        r"\b((?:19|20)\d{2})\b",
    ]:
        m = re.search(pat, first_page)
        if m:
            result["year"] = int(m.group(1))
            break

    # Title — first substantive lines before abstract / venue headers
    lines = first_page.splitlines()
    title_lines: list[str] = []
    for line in lines[:30]:
        line = line.strip()
        if not line:
            if title_lines:
                break
            continue
        if re.match(r"(?i)\babstract\b", line):
            break
        if len(line) < 8 or _VENUE_SKIP_RE.search(line):
            if title_lines:
                break
            continue
        title_lines.append(line)
        if len(title_lines) == 3:
            break
    if title_lines:
        result["title"] = " ".join(title_lines)

    # Authors — first name-like line after the title block
    if result["title"]:
        title_end = first_page.find(title_lines[-1]) + len(title_lines[-1])
        block = first_page[title_end: title_end + 400]
        for line in block.splitlines():
            line = re.sub(r"[\d\*†‡§¶]+$", "", line.strip()).strip()
            if len(line) < 5 or re.match(r"(?i)\babstract\b", line):
                break
            if _NAME_RE.match(line):
                # Split "A, B and C" into a list
                parts = re.split(r",\s*|\s+and\s+", line)
                result["authors"] = [p.strip() for p in parts if p.strip()]
                break

    # Venue — first line that looks like a venue name
    for line in lines[:40]:
        line = line.strip()
        if _VENUE_LINE_RE.search(line) and len(line) > 10:
            result["venue"] = line
            if re.search(r"(?i)\barxiv\b", line):
                result["venue_type"] = "preprint"
            elif re.search(r"(?i)(proceedings|conference|workshop|symposium)", line):
                result["venue_type"] = "proceedings-article"
            elif re.search(r"(?i)(transactions|journal|letters)", line):
                result["venue_type"] = "journal-article"
            break

    return result


# ---------------------------------------------------------------------------
# Public bibliographic entry point
# ---------------------------------------------------------------------------

def _extract_bibliographic(first_page: str, mailto: str | None = None) -> dict:
    """Return bibliographic metadata, preferring Crossref over heuristics."""
    doi = _extract_doi(first_page)

    if doi:
        logger.debug("    DOI found: %s — querying Crossref", doi)
        crossref = _fetch_crossref(doi, mailto=mailto)
        if crossref:
            return {
                "doi": doi,
                "source": "crossref",
                **crossref,
            }
        logger.debug("    Crossref lookup failed for %s — falling back to heuristics", doi)

    bio = _heuristic_bibliographic(first_page)
    bio["doi"] = doi  # keep the DOI even if Crossref failed
    return bio


# ---------------------------------------------------------------------------
# OCR fallback
# ---------------------------------------------------------------------------

def _ocr_fallback(pdf_path: Path, max_pages: int) -> str:
    """Return OCR'd text for *pdf_path* using pytesseract + pdf2image."""
    try:
        from pdf2image import convert_from_path  # type: ignore[import]
        import pytesseract  # type: ignore[import]
    except ImportError:
        logger.warning(
            "OCR fallback requested but pdf2image/pytesseract are not installed. "
            "Run: pip install pdf2image pytesseract"
        )
        return ""

    logger.info("    OCR fallback: converting pages to images...")
    try:
        images = convert_from_path(pdf_path, last_page=max_pages, dpi=200)
        pages_text = [pytesseract.image_to_string(img) for img in images]
        return "\n".join(pages_text)
    except Exception as exc:  # noqa: BLE001
        logger.error("    OCR failed for %s: %s", pdf_path.name, exc)
        return ""


def _tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(
    pdf_path: Path,
    use_ocr: bool = False,
    crossref_mailto: str | None = None,
) -> dict:
    """Return extraction result dict for *pdf_path*."""
    result: dict = {
        "filename": pdf_path.name,
        "pages_extracted": 0,
        "full_text": "",
        "abstract": None,
        "text_for_classification": "",
        "bibliographic": dict(_EMPTY_BIBLIOGRAPHIC),
        "ocr_used": False,
        "error": None,
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages_text = []
            for page in pdf.pages[:MAX_PAGES]:
                page_text = page.extract_text() or ""
                pages_text.append(page_text)

            result["pages_extracted"] = len(pages_text)
            result["full_text"] = "\n".join(pages_text)
            first_page = pages_text[0] if pages_text else ""

        # OCR fallback when text layer is absent or minimal
        if use_ocr and len(result["full_text"].strip()) < MIN_TEXT_FOR_OCR:
            logger.warning(
                "  Text too short (%d chars) for %s — triggering OCR fallback",
                len(result["full_text"].strip()),
                pdf_path.name,
            )
            ocr_text = _ocr_fallback(pdf_path, MAX_PAGES)
            if ocr_text.strip():
                result["full_text"] = ocr_text
                first_page = ocr_text.split("\n\n")[0]
                result["ocr_used"] = True
            else:
                logger.warning("  OCR produced no text for %s", pdf_path.name)
        elif len(result["full_text"].strip()) < MIN_TEXT_FOR_OCR:
            logger.warning(
                "  Text too short (%d chars) for %s — consider re-running with --ocr",
                len(result["full_text"].strip()),
                pdf_path.name,
            )

        result["bibliographic"] = _extract_bibliographic(first_page, mailto=crossref_mailto)
        if result["bibliographic"].get("doi"):
            logger.info(
                "    bibliographic: source=%s doi=%s year=%s",
                result["bibliographic"]["source"],
                result["bibliographic"]["doi"],
                result["bibliographic"]["year"],
            )

        abstract = _extract_abstract(result["full_text"])
        result["abstract"] = abstract

        if abstract:
            result["text_for_classification"] = abstract
        else:
            words = result["full_text"].split()
            result["text_for_classification"] = " ".join(
                words[:MAX_WORDS_FOR_CLASSIFICATION]
            )

    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        logger.error("Error extracting %s: %s", pdf_path.name, exc)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Extract text from PDF papers for downstream classification."
    )
    parser.add_argument(
        "--papers-dir",
        type=Path,
        default=PAPERS_DIR,
        help="Directory containing PDF papers (default: papers/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=EXTRACTED_DIR,
        help="Directory to save extracted JSON files (default: out/extracted/)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-extract PDFs that already have an output file.",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        default=_tesseract_available(),
        help=(
            "Enable OCR fallback for image-only PDFs using pytesseract + pdf2image "
            "(auto-enabled when tesseract is detected on PATH; default: "
            f"{'on' if _tesseract_available() else 'off'} on this machine)"
        ),
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Disable OCR fallback even if tesseract is available.",
    )
    parser.add_argument(
        "--mailto",
        default=os.environ.get("CROSSREF_MAILTO"),
        metavar="EMAIL",
        help=(
            "E-mail address for the Crossref polite pool (higher rate limits). "
            "Can also be set via the CROSSREF_MAILTO environment variable."
        ),
    )
    parser.add_argument(
        "--no-crossref",
        action="store_true",
        help="Skip Crossref API lookups and rely only on heuristic extraction.",
    )
    args = parser.parse_args(argv)

    use_ocr = args.ocr and not args.no_ocr
    mailto = None if args.no_crossref else args.mailto

    if not args.no_crossref and not mailto:
        logger.info(
            "Tip: set --mailto or CROSSREF_MAILTO=your@email.com to join the "
            "Crossref polite pool for faster lookups."
        )

    if not args.papers_dir.exists():
        logger.error("Papers directory not found: %s", args.papers_dir)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(args.papers_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning("No PDF files found in %s", args.papers_dir)
        sys.exit(0)

    logger.info("Found %d PDF file(s) to process.", len(pdf_files))
    if use_ocr:
        logger.info("OCR fallback enabled.")
    if args.no_crossref:
        logger.info("Crossref lookups disabled (--no-crossref).")

    success = errors = skipped = 0

    for pdf_path in pdf_files:
        output_path = args.output_dir / f"{pdf_path.stem}.json"

        if output_path.exists() and not args.overwrite:
            logger.info("  skip (already extracted): %s", pdf_path.name)
            skipped += 1
            continue

        logger.info("  extracting: %s", pdf_path.name)
        result = extract_text_from_pdf(
            pdf_path,
            use_ocr=use_ocr,
            crossref_mailto=mailto,
        )

        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)

        if result["error"]:
            errors += 1
        else:
            success += 1

    logger.info(
        "Extraction complete — success: %d  errors: %d  skipped: %d",
        success,
        errors,
        skipped,
    )


if __name__ == "__main__":
    main()
