"""Step 1 – Extract text from PDF papers (deterministic, no LLM).

For each PDF found in ``papers/``, this script:
  1. Reads up to ``MAX_PAGES`` pages with *pdfplumber*.
  2. Falls back to OCR (pytesseract + pdf2image) if text layer is empty/minimal
     and ``--ocr`` is requested or tesseract is auto-detected.
  3. Tries to isolate the **abstract** using common section-header patterns.
  4. Writes a JSON file to ``data/extracted/`` that downstream scripts use.

The JSON payload per paper:
  - ``filename``              – original PDF file name
  - ``pages_extracted``       – number of pages actually read
  - ``full_text``             – concatenated page text
  - ``abstract``              – detected abstract section (or ``null``)
  - ``text_for_classification`` – abstract if found; else first
                                  ``MAX_WORDS_FOR_CLASSIFICATION`` words
  - ``ocr_used``              – True when OCR fallback was triggered
  - ``error``                 – error message if extraction failed
"""

import argparse
import json
import logging
import re
import shutil
import sys
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
EXTRACTED_DIR = Path("data/extracted")
MAX_PAGES = 10
MAX_WORDS_FOR_CLASSIFICATION = 1500
MIN_ABSTRACT_LENGTH = 100
MIN_TEXT_FOR_OCR = 200  # characters below this threshold triggers OCR fallback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_abstract(text: str) -> str | None:
    """Return the abstract section from *text*, or ``None`` if not found."""
    patterns = [
        # IEEE inline: "Abstract—" or "Abstract—" (em-dash / en-dash)
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


def extract_text_from_pdf(pdf_path: Path, use_ocr: bool = False) -> dict:
    """Return extraction result dict for *pdf_path*."""
    result: dict = {
        "filename": pdf_path.name,
        "pages_extracted": 0,
        "full_text": "",
        "abstract": None,
        "text_for_classification": "",
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
                result["ocr_used"] = True
            else:
                logger.warning("  OCR produced no text for %s", pdf_path.name)
        elif len(result["full_text"].strip()) < MIN_TEXT_FOR_OCR:
            logger.warning(
                "  Text too short (%d chars) for %s — "
                "consider re-running with --ocr",
                len(result["full_text"].strip()),
                pdf_path.name,
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
        help="Directory to save extracted JSON files (default: data/extracted/)",
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
    args = parser.parse_args(argv)

    use_ocr = args.ocr and not args.no_ocr

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
        logger.info("OCR fallback enabled (tesseract detected).")

    success = errors = skipped = 0

    for pdf_path in pdf_files:
        output_path = args.output_dir / f"{pdf_path.stem}.json"

        if output_path.exists() and not args.overwrite:
            logger.info("  skip (already extracted): %s", pdf_path.name)
            skipped += 1
            continue

        logger.info("  extracting: %s", pdf_path.name)
        result = extract_text_from_pdf(pdf_path, use_ocr=use_ocr)

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
