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
OUTPUT_DIR = _PROJECT_ROOT / "out" / "organized_papers"
REPORT_PDF = _PROJECT_ROOT / "out" / "script" / "qse_swebok_report (2).pdf"

def sanitize_folder_name(name: str) -> str:
    """Sanitize subject name to be used as a folder name."""
    return "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()

def normalize_title(title: str) -> str:
    return re.sub(r'[^a-z0-9]', '', title.lower())

def extract_classifications_from_report(report_path: Path) -> dict[str, list[str]]:
    if pdfplumber is None:
        logger.error("pdfplumber is not installed. Run: pip install pdfplumber")
        return {}

    logger.info("Extracting classifications from %s", report_path.name)
    text = ""
    with pdfplumber.open(report_path) as pdf:
        for page in pdf.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + '\n'
            
    subjects = {}
    current_subject = None
    in_articles_list = False
    current_title_lines = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        
        # Check for subject header (e.g. 4.2 Software Architecture)
        m_subj = re.match(r'^4\.\d+\s+(Software\s+.*)$', line)
        if m_subj:
            current_subject = m_subj.group(1)
            in_articles_list = False
            continue
            
        if line == "Artigos classificados nesta área":
            in_articles_list = True
            continue
            
        if in_articles_list and current_subject:
            # Check if line starts with a number and dot, e.g. "1. Title"
            m_title_start = re.match(r'^\d+\.\s+(.*)$', line)
            if m_title_start:
                current_title_lines = [m_title_start.group(1)]
            elif current_title_lines:
                # Check if this line is the author line (starts with year)
                if (re.match(r'^20\d\d\s+•', line) or 
                    (len(line) > 4 and line[:4].isdigit() and '•' in line) or 
                    re.match(r'^20\d\d\s*\(cid:127\)', line)):
                    full_title = ' '.join(current_title_lines)
                    norm_t = normalize_title(full_title)
                    if norm_t not in subjects:
                        subjects[norm_t] = []
                    if current_subject not in subjects[norm_t]:
                        subjects[norm_t].append(current_subject)
                    current_title_lines = []
                else:
                    current_title_lines.append(line)
                    
    logger.info("Extracted %d paper classifications from report.", len(subjects))
    return subjects

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Organize PDF papers into subfolders by their primary subject based on a PDF report."
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
        "--report-pdf",
        type=Path,
        default=REPORT_PDF,
        help="Path to the PDF report containing the classifications."
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

    if not args.report_pdf.exists():
        logger.error("Report PDF not found: %s", args.report_pdf)
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)
    
    subjects_map = extract_classifications_from_report(args.report_pdf)
    if not subjects_map:
        logger.error("No classifications found in the report.")
        return

    extracted_files = list(args.extracted_dir.glob("*.json"))
    
    success = 0
    errors = 0
    unclassified = 0

    for extracted_path in extracted_files:
        try:
            with open(extracted_path, encoding="utf-8") as fh:
                data = json.load(fh)
                
            title = data.get("title")
            if not title and "bibliographic" in data and isinstance(data["bibliographic"], dict):
                title = data["bibliographic"].get("title")
                
            if not title:
                # If no title in JSON, fallback to stem
                title = extracted_path.stem
                
            norm_title = normalize_title(title)
            primary_subjects = []
            
            if norm_title in subjects_map:
                primary_subjects = subjects_map[norm_title]
            else:
                for rep_title, subjs in subjects_map.items():
                    if rep_title in norm_title or norm_title in rep_title:
                        primary_subjects = subjs
                        break
                        
            if not primary_subjects:
                primary_subjects = ["Unclassified"]
                unclassified += 1
                
            filename = data.get("filename")
            if not filename:
                filename = f"{extracted_path.stem}.pdf"
                
            source_pdf = args.papers_dir / filename
            if not source_pdf.exists():
                source_pdf = args.papers_dir / f"{extracted_path.stem}.pdf"
                
            if not source_pdf.exists():
                logger.warning("PDF not found for %s", filename)
                errors += 1
                continue
                
            for i, subj in enumerate(primary_subjects):
                folder_name = sanitize_folder_name(subj)
                target_folder = args.output_dir / folder_name
                target_folder.mkdir(parents=True, exist_ok=True)
                
                target_pdf = target_folder / source_pdf.name
                
                if target_pdf.exists() and not args.move:
                    logger.info("Already exists: %s in %s", target_pdf.name, target_folder.name)
                    continue
                    
                if args.move and i == len(primary_subjects) - 1:
                    # Move only on the last subject to avoid missing file errors for subsequent copies
                    if not target_pdf.exists():
                        shutil.move(source_pdf, target_pdf)
                        logger.info("Moved %s -> %s", source_pdf.name, target_folder.name)
                else:
                    if not target_pdf.exists():
                        shutil.copy2(source_pdf, target_pdf)
                        logger.info("Copied %s -> %s", source_pdf.name, target_folder.name)
            
            success += 1
            
        except Exception as exc:
            logger.error("Error processing %s: %s", extracted_path.name, exc)
            errors += 1

    action_word = "moved" if args.move else "copied"
    logger.info("Finished organizing papers. Successfully %s: %d, Unclassified: %d, Errors: %d", 
                action_word, success, unclassified, errors)

if __name__ == "__main__":
    main()
