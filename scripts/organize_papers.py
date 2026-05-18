"""Organize papers into folders based on their primary subject from the PDF report."""

import argparse
import json
import logging
import shutil
import re
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
PAPERS_DIR = _PROJECT_ROOT / "papers"
EXTRACTED_DIR = _PROJECT_ROOT / "out" / "extracted"
CLASSIFICATIONS_DIR = _PROJECT_ROOT / "out" / "classifications"
OUTPUT_DIR = _PROJECT_ROOT / "out" / "organized_papers"
REPORT_PDF = _PROJECT_ROOT / "out" / "script" / "qse_swebok_report (2).pdf"

def sanitize_folder_name(name: str) -> str:
    """Sanitize subject name to be used as a folder name."""
    return "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()

def normalize_title(title: str) -> str:
    return re.sub(r'[^a-z0-9]', '', title.lower())

def load_classifications(classifications_dir: Path) -> dict:
    """Load per-paper classification JSONs from `out/classifications/`.

    Returns a mapping from stem -> classification dict (as written by
    `scripts/classify.py`).
    """
    if not classifications_dir.exists():
        logger.warning("Classifications directory not found: %s", classifications_dir)
        return {}

    result: dict[str, dict] = {}
    for p in classifications_dir.glob("*.json"):
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            stem = data.get("stem") or p.stem
            result[stem] = data
        except Exception:
            logger.exception("Failed to load classification: %s", p.name)

    logger.info("Loaded %d classification files.", len(result))
    return result


def load_topics(topics_path: Path) -> dict:
    """Load the qse_swebok_topics.json mapping and return mapping title->subjects.

    The returned dict maps normalized title -> list of subject names.
    """
    if not topics_path.exists():
        logger.info("Topics file not found: %s", topics_path)
        return {}

    try:
        with open(topics_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        logger.exception("Failed to read topics file: %s", topics_path)
        return {}

    mapping: dict[str, list[str]] = {}
    for subject, body in data.items():
        artigos = body.get("artigos") or []
        for art in artigos:
            titulo = art.get("titulo") or ""
            if not titulo:
                continue
            key = normalize_title(titulo)
            if key not in mapping:
                mapping[key] = []
            if subject not in mapping[key]:
                mapping[key].append(subject)

    logger.info("Loaded %d topics from %s", len(mapping), topics_path.name)
    return mapping

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Organize PDF papers and extracted texts into subfolders by subject using out/classifications/*.json."
    )
    parser.add_argument(
        "--papers-dir",
        type=Path,
        default=PAPERS_DIR,
        help="Directory with original PDF files."
    )
    parser.add_argument(
        "--extracted-dir",
        type=Path,
        default=EXTRACTED_DIR,
        help="Directory with extracted metadata JSON files."
    )
    parser.add_argument(
        "--classifications-dir",
        type=Path,
        default=CLASSIFICATIONS_DIR,
        help="Directory with per-paper classification JSON files (default: out/classifications/)."
    )
    parser.add_argument(
        "--topics-file",
        type=Path,
        default=_PROJECT_ROOT / "out" / "script" / "qse_swebok_topics.json",
        help="Path to qse_swebok_topics.json (default: out/script/qse_swebok_topics.json)."
    )
    parser.add_argument(
        "--include-extracted",
        action="store_true",
        help="Also copy the extracted JSON/text from --extracted-dir into each subject folder."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory to copy organized papers into (default: out/organized_papers/)."
    )
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move the files instead of copying them. Note: if a file belongs to multiple subjects, only the first will be moved, the rest will be copied."
    )
    
    args = parser.parse_args(argv)

    if not args.papers_dir.exists():
        logger.error("Papers directory not found: %s", args.papers_dir)
        return

    if not args.classifications_dir.exists():
        logger.error("Classifications directory not found: %s", args.classifications_dir)
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)

    classifications = load_classifications(args.classifications_dir)
    topics_map = load_topics(args.topics_file) if args.topics_file else {}

    success = 0
    errors = 0
    unclassified = 0

    extracted_files = list(args.extracted_dir.glob("*.json"))
    if not extracted_files:
        logger.warning("No extracted JSON files found in %s", args.extracted_dir)

    for extracted_path in extracted_files:
        try:
            with open(extracted_path, encoding="utf-8") as fh:
                data = json.load(fh)

            title = data.get("title")
            if not title and "bibliographic" in data and isinstance(data["bibliographic"], dict):
                title = data["bibliographic"].get("title")

            if not title:
                title = extracted_path.stem

            norm_title = normalize_title(title)

            subjects: list[str] = []

            # 1) priority: topics file
            if norm_title in topics_map:
                subjects = topics_map[norm_title]

            # 2) fallback: try partial match in topics
            if not subjects:
                for k, v in topics_map.items():
                    if k in norm_title or norm_title in k:
                        subjects = v
                        break

            # 3) fallback: use classification JSONs
            if not subjects:
                clf_wrapper = classifications.get(extracted_path.stem)
                if clf_wrapper:
                    clf = clf_wrapper.get("classification") or {}
                    subjects = clf.get("subjects") or []

            if not subjects:
                subjects = ["Unclassified"]
                unclassified += 1

            filename = data.get("filename") or f"{extracted_path.stem}.pdf"
            source_pdf = args.papers_dir / filename
            if not source_pdf.exists():
                source_pdf = args.papers_dir / f"{extracted_path.stem}.pdf"

            if not source_pdf.exists():
                logger.warning("PDF not found for %s (stem=%s)", filename, extracted_path.stem)
                errors += 1
                continue

            extracted_src = extracted_path

            for i, subj in enumerate(subjects):
                folder_name = sanitize_folder_name(subj)
                target_folder = args.output_dir / folder_name
                target_folder.mkdir(parents=True, exist_ok=True)

                target_pdf = target_folder / source_pdf.name

                if target_pdf.exists() and not args.move:
                    logger.info("Already exists: %s in %s", target_pdf.name, target_folder.name)
                else:
                    if args.move and i == len(subjects) - 1:
                        if not target_pdf.exists():
                            shutil.move(source_pdf, target_pdf)
                            logger.info("Moved %s -> %s", source_pdf.name, target_folder.name)
                    else:
                        if not target_pdf.exists():
                            shutil.copy2(source_pdf, target_pdf)
                            logger.info("Copied %s -> %s", source_pdf.name, target_folder.name)

                if args.include_extracted and extracted_src.exists():
                    target_extracted = target_folder / extracted_src.name
                    if not target_extracted.exists():
                        shutil.copy2(extracted_src, target_extracted)

            success += 1

        except Exception as exc:
            logger.error("Error processing %s: %s", extracted_path.name, exc)
            errors += 1

    action_word = "moved" if args.move else "copied"
    logger.info(
        "Finished organizing papers. Successfully %s: %d, Unclassified: %d, Errors: %d",
        action_word,
        success,
        unclassified,
        errors,
    )

if __name__ == "__main__":
    main()
