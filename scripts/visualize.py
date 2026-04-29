"""Step 3 – Aggregate classifications and generate a histogram (deterministic).

Reads all classification JSON files produced by ``classify.py`` and:
  1. Counts how many papers mention each SE knowledge area.
  2. Saves a ``paper_subjects.csv`` table with per-paper details.
  3. Saves a ``subject_frequencies.json`` with aggregate counts.
  4. Saves a ``histogram.png`` horizontal bar chart sorted by frequency.

No LLM API calls are made; this step is fully deterministic.
"""

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

CLASSIFICATIONS_DIR = Path("data/classifications")
OUTPUT_DIR = Path("data/output")

# Chart layout constants
BAR_HEIGHT_FACTOR = 0.5    # inches of figure height per bar
LABEL_PADDING_FACTOR = 1.12  # extra horizontal space for bar-end labels


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_classifications(classifications_dir: Path) -> list[dict]:
    """Return a list of valid classification records from *classifications_dir*."""
    records = []
    for path in sorted(classifications_dir.glob("*.json")):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        clf = data.get("classification", {})
        if "error" not in clf:
            records.append(data)
        else:
            logger.warning("Skipping file with error: %s", path.name)
    return records


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate SE subject classifications and produce a histogram "
            "showing which topics are most common in the QSE literature."
        )
    )
    parser.add_argument(
        "--classifications-dir",
        type=Path,
        default=CLASSIFICATIONS_DIR,
        help="Directory with classification JSON files (default: data/classifications/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory to save output files (default: data/output/)",
    )
    parser.add_argument(
        "--title",
        default="SE Knowledge Areas in QSE Literature",
        help='Histogram title (default: "SE Knowledge Areas in QSE Literature")',
    )
    args = parser.parse_args(argv)

    if not args.classifications_dir.exists():
        logger.error(
            "Classifications directory not found: %s", args.classifications_dir
        )
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)

    records = load_classifications(args.classifications_dir)
    if not records:
        logger.warning("No valid classifications found. Nothing to visualise.")
        return

    logger.info("Loaded %d classification record(s).", len(records))

    # -----------------------------------------------------------------------
    # Aggregate
    # -----------------------------------------------------------------------
    subject_counter: Counter = Counter()
    paper_rows = []

    for item in records:
        clf = item.get("classification", {})
        subjects: list[str] = clf.get("subjects", [])

        for subject in subjects:
            subject_counter[subject] += 1

        paper_rows.append(
            {
                "filename": item.get("filename", ""),
                "subjects": "; ".join(subjects),
                "primary_subject": clf.get("primary_subject", ""),
                "summary": clf.get("summary", ""),
                "confidence": clf.get("confidence", ""),
            }
        )

    # -----------------------------------------------------------------------
    # Save CSV
    # -----------------------------------------------------------------------
    csv_path = args.output_dir / "paper_subjects.csv"
    df = pd.DataFrame(paper_rows)
    df.to_csv(csv_path, index=False)
    logger.info("Saved per-paper table to %s", csv_path)

    # -----------------------------------------------------------------------
    # Save frequency JSON
    # -----------------------------------------------------------------------
    freq_data = [
        {"subject": subject, "count": count}
        for subject, count in subject_counter.most_common()
    ]
    freq_path = args.output_dir / "subject_frequencies.json"
    with open(freq_path, "w", encoding="utf-8") as fh:
        json.dump(freq_data, fh, indent=2)
    logger.info("Saved frequency table to %s", freq_path)

    # -----------------------------------------------------------------------
    # Generate histogram
    # -----------------------------------------------------------------------
    if subject_counter:
        subjects = [entry["subject"] for entry in freq_data]
        counts = [entry["count"] for entry in freq_data]

        fig, ax = plt.subplots(figsize=(12, max(6, len(subjects) * BAR_HEIGHT_FACTOR)))
        bars = ax.barh(subjects, counts, color="steelblue")
        ax.bar_label(bars, padding=4, fontsize=9)
        ax.set_xlabel("Number of Papers")
        ax.set_title(args.title)
        ax.invert_yaxis()  # most frequent at the top
        ax.set_xlim(right=max(counts) * LABEL_PADDING_FACTOR)  # room for labels
        plt.tight_layout()

        histogram_path = args.output_dir / "histogram.png"
        plt.savefig(histogram_path, dpi=150, bbox_inches="tight")
        plt.close()
        logger.info("Saved histogram to %s", histogram_path)

    # -----------------------------------------------------------------------
    # Print summary table to stdout
    # -----------------------------------------------------------------------
    logger.info("\n%-45s  %s", "SE Knowledge Area", "Papers")
    logger.info("-" * 55)
    for subject, count in subject_counter.most_common():
        logger.info("  %-43s  %d", subject, count)


if __name__ == "__main__":
    main()
