"""Extract text and metadata from local PDF papers.

This script is useful on its own, but its extraction helpers are also reused by
``enrich_from_pdfs.py`` to improve records that were initially created from the
spreadsheet/API pipeline.
"""

from __future__ import annotations

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
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PAPERS_DIR = Path("papers")
EXTRACTED_DIR = Path("out/extracted")
MAX_PAGES = 10
MAX_WORDS_FOR_CLASSIFICATION = 1500
MIN_ABSTRACT_LENGTH = 100
MIN_TEXT_FOR_OCR = 200
CROSSREF_TIMEOUT = 8

_EMPTY_BIBLIOGRAPHIC: dict = {
    "doi": None,
    "title": None,
    "year": None,
    "authors": None,
    "venue": None,
    "venue_type": None,
    "source": "heuristic",
}
_NOISY_LINE_RE = [
    re.compile(r"^version of record:", re.IGNORECASE),
    re.compile(r"^contents lists available at", re.IGNORECASE),
    re.compile(r"^available online at", re.IGNORECASE),
    re.compile(r"^sciencedirect$", re.IGNORECASE),
    re.compile(r"^article info$", re.IGNORECASE),
    re.compile(r"^please cite this article", re.IGNORECASE),
    re.compile(r"^this is a pdf file of an unedited manuscript", re.IGNORECASE),
]


def _extract_abstract(text: str) -> str | None:
    """Return the abstract section from *text*, or ``None`` if not found."""
    patterns = [
        r"(?i)\babstract\s*[—–-]\s*(.*?)(?=\n\s*(?:keywords|index\s+terms|i+\.\s+introduction|1[\.\s]+introduction)|\Z)",
        r"(?i)\babstract\b[\s:—–-]*\n+(.*?)(?=\n\s*\n|\n\s*(?:keywords|index\s+terms|introduction))",
        r"(?i)\babstract:\s+(.*?)(?=\n\s*\n|\n\s*(?:keywords|index\s+terms|introduction))",
        r"(?i)\babstract\.\s+(.*?)(?=\n\s*\n|\n\s*(?:keywords|introduction))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            continue
        abstract = re.sub(r"\s+", " ", match.group(1)).strip()
        if len(abstract) > MIN_ABSTRACT_LENGTH:
            return abstract
    return None


def _clean_extracted_text(text: str) -> str:
    """Normalize PDF text before metadata and abstract extraction."""
    if not text:
        return ""

    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"(\w)-\n(\w)", r"\1\2", cleaned)
    cleaned = re.sub(r"\(cid:\d+\)", " ", cleaned)
    cleaned = re.sub(r"\bManuscript_[a-f0-9]+\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bARTICLE INFO\b", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bABSTRACT\b", "\nAbstract\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(KEYWORDS?|Index Terms)\b\s*:?", r"\n\1: ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?m)^\d+\s+(?=[A-Z][A-Za-z])", "", cleaned)

    normalized_lines: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            if normalized_lines and normalized_lines[-1]:
                normalized_lines.append("")
            continue

        line = re.sub(r"(?<=[,;:])(?=\S)", " ", line)
        line = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", line)
        line = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", line)
        line = re.sub(r"\s+", " ", line).strip()
        if any(pattern.search(line) for pattern in _NOISY_LINE_RE):
            continue
        normalized_lines.append(line)

    cleaned = "\n".join(normalized_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


_DOI_RE = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*|DOI:\s*)?"
    r"(10\.\d{4,9}/[^\s,;\"'<>\[\]{}()]+)",
    re.IGNORECASE,
)


def _extract_doi(text: str) -> str | None:
    """Return the first DOI found in *text*."""
    match = _DOI_RE.search(text)
    if not match:
        return None
    return match.group(1).rstrip(".")


def _fetch_crossref(doi: str, mailto: str | None = None) -> dict | None:
    """Query CrossRef for metadata of *doi*."""
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
        logger.debug("CrossRef lookup failed for %s: %s", doi, exc)
        return None

    msg = data.get("message", {})
    titles = msg.get("title") or []
    authors_raw = msg.get("author") or []
    date_parts = (msg.get("published") or msg.get("published-print") or {}).get(
        "date-parts",
        [[]],
    )
    venues = msg.get("container-title") or []

    authors: list[str] | None = [
        " ".join(filter(None, [author.get("given"), author.get("family")])).strip()
        for author in authors_raw
        if author.get("family")
    ] or None

    year = date_parts[0][0] if date_parts and date_parts[0] else None

    return {
        "title": titles[0] if titles else None,
        "year": year,
        "authors": authors,
        "venue": venues[0] if venues else None,
        "venue_type": msg.get("type"),
    }


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
    """Extract title, year, authors and venue via simple heuristics."""
    result = dict(_EMPTY_BIBLIOGRAPHIC)

    for pattern in [
        r"©\s*((?:19|20)\d{2})\b",
        r"[Cc]opyright\s+(?:©\s*)?((?:19|20)\d{2})\b",
        r"\b((?:19|20)\d{2})\s+IEEE\b",
        r"\bIEEE\s+((?:19|20)\d{2})\b",
        r"\bPublished\s+(?:in\s+)?((?:19|20)\d{2})\b",
        r"\b((?:19|20)\d{2})\b",
    ]:
        match = re.search(pattern, first_page)
        if match:
            result["year"] = int(match.group(1))
            break

    lines = first_page.splitlines()
    title_lines: list[str] = []
    for raw_line in lines[:30]:
        line = raw_line.strip()
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

    if result["title"]:
        title_end = first_page.find(title_lines[-1]) + len(title_lines[-1])
        block = first_page[title_end : title_end + 400]
        for raw_line in block.splitlines():
            line = re.sub(r"[\d*†‡§¶]+$", "", raw_line.strip()).strip()
            if len(line) < 5 or re.match(r"(?i)\babstract\b", line):
                break
            if _NAME_RE.match(line):
                result["authors"] = [
                    part.strip()
                    for part in re.split(r",\s*|\s+and\s+", line)
                    if part.strip()
                ]
                break

    for raw_line in lines[:40]:
        line = raw_line.strip()
        if not (_VENUE_LINE_RE.search(line) and len(line) > 10):
            continue
        result["venue"] = line
        if re.search(r"(?i)\barxiv\b", line):
            result["venue_type"] = "preprint"
        elif re.search(r"(?i)(proceedings|conference|workshop|symposium)", line):
            result["venue_type"] = "proceedings-article"
        elif re.search(r"(?i)(transactions|journal|letters)", line):
            result["venue_type"] = "journal-article"
        break

    return result


def _extract_bibliographic(
    first_page: str,
    *,
    mailto: str | None = None,
    use_crossref: bool = True,
) -> dict:
    """Return bibliographic metadata, preferring CrossRef when enabled."""
    doi = _extract_doi(first_page)
    if doi and use_crossref:
        crossref = _fetch_crossref(doi, mailto=mailto)
        if crossref:
            return {"doi": doi, "source": "crossref", **crossref}

    bio = _heuristic_bibliographic(first_page)
    bio["doi"] = doi
    return bio


def _ocr_fallback(pdf_path: Path, max_pages: int) -> str:
    """Return OCR text for *pdf_path* using pdf2image + pytesseract."""
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
    except (OSError, ValueError) as exc:
        logger.error("    Failed to render pages for OCR on %s: %s", pdf_path.name, exc)
        return ""

    pages_text: list[str] = []
    for image in images:
        pages_text.append(pytesseract.image_to_string(image))
    return "\n".join(pages_text)


def _tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def extract_text_from_pdf(
    pdf_path: Path,
    *,
    use_ocr: bool = False,
    crossref_mailto: str | None = None,
    use_crossref: bool = True,
    max_pages: int = MAX_PAGES,
) -> dict:
    """Return an extracted JSON-compatible payload for *pdf_path*."""
    result: dict = {
        "filename": pdf_path.name,
        "stem": pdf_path.stem,
        "pages_extracted": 0,
        "full_text": "",
        "abstract": None,
        "text_for_classification": "",
        "bibliographic": dict(_EMPTY_BIBLIOGRAPHIC),
        "ocr_used": False,
        "error": None,
        "extract_text": {
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages_text = []
            for page in pdf.pages[:max_pages]:
                pages_text.append(page.extract_text() or "")
    except (OSError, ValueError, TypeError) as exc:
        result["error"] = str(exc)
        logger.error("Error extracting %s: %s", pdf_path.name, exc)
        return result

    result["pages_extracted"] = len(pages_text)
    cleaned_pages = [_clean_extracted_text(page_text) for page_text in pages_text]
    result["full_text"] = "\n\n".join(page for page in cleaned_pages if page)
    first_page = cleaned_pages[0] if cleaned_pages else ""

    if use_ocr and len(result["full_text"].strip()) < MIN_TEXT_FOR_OCR:
        logger.warning(
            "  Text too short (%d chars) for %s — triggering OCR fallback",
            len(result["full_text"].strip()),
            pdf_path.name,
        )
        ocr_text = _ocr_fallback(pdf_path, max_pages)
        if ocr_text.strip():
            result["full_text"] = _clean_extracted_text(ocr_text)
            first_page = result["full_text"].split("\n\n")[0]
            result["ocr_used"] = True
    elif len(result["full_text"].strip()) < MIN_TEXT_FOR_OCR:
        logger.warning(
            "  Text too short (%d chars) for %s — consider re-running with --ocr",
            len(result["full_text"].strip()),
            pdf_path.name,
        )

    result["bibliographic"] = _extract_bibliographic(
        first_page,
        mailto=crossref_mailto,
        use_crossref=use_crossref,
    )
    abstract = _extract_abstract(result["full_text"])
    result["abstract"] = abstract

    if abstract:
        result["text_for_classification"] = abstract
    else:
        words = result["full_text"].split()
        result["text_for_classification"] = " ".join(words[:MAX_WORDS_FOR_CLASSIFICATION])

    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Extract text from PDF papers.")
    parser.add_argument(
        "--papers-dir",
        type=Path,
        default=PAPERS_DIR,
        help="Directory containing PDF papers (default: papers/).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=EXTRACTED_DIR,
        help="Directory to save extracted JSON files (default: out/extracted/).",
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
            f"(default: {'on' if _tesseract_available() else 'off'} on this machine)."
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
        help="Email address for the CrossRef polite pool.",
    )
    parser.add_argument(
        "--no-crossref",
        action="store_true",
        help="Skip CrossRef lookups and rely only on heuristic extraction.",
    )
    args = parser.parse_args(argv)

    use_ocr = args.ocr and not args.no_ocr
    mailto = None if args.no_crossref else args.mailto

    if not args.papers_dir.exists():
        logger.error("Papers directory not found: %s", args.papers_dir)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    pdf_files = sorted(args.papers_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning("No PDF files found in %s", args.papers_dir)
        sys.exit(0)

    success = 0
    skipped = 0
    errors = 0

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
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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
