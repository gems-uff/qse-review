"""Step 3 – Aggregate classifications and generate a histogram (deterministic).

Reads all classification JSON files produced by ``classify.py`` and:
  1. Counts how many papers mention each SE knowledge area.
  2. Saves a ``paper_subjects.csv`` table with per-paper details.
  3. Saves a ``subject_frequencies.json`` with aggregate counts.
  4. Saves a ``histogram.png`` horizontal bar chart sorted by frequency
     (all 15 SWEBOK areas always shown; areas with 0 papers are gaps).
  5. Saves a ``cooccurrence.png`` heatmap showing which subject pairs
     tend to appear together.
  6. Saves a ``cooccurrence.json`` with the raw co-occurrence matrix.

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

CLASSIFICATIONS_DIR = Path("out/classifications")
SWEBOK_SUBJECTS_PATH = Path("swebok_subjects.json")
OUTPUT_DIR = Path("out/analysis")

VALID_CONFIDENCE = {"high", "medium", "low"}

# Chart layout constants
BAR_HEIGHT_FACTOR = 0.5
LABEL_PADDING_FACTOR = 1.12


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_se_subjects() -> list[str]:
    if SWEBOK_SUBJECTS_PATH.exists():
        with open(SWEBOK_SUBJECTS_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return []


SE_SUBJECTS: list[str] = _load_se_subjects()
SE_SUBJECTS_SET: set[str] = set(SE_SUBJECTS)
VALID_CONFIDENCE = {"high", "medium", "low"}


def _validate_classification(clf: dict, stem: str) -> list[str]:
    """Return validation error strings (empty = valid)."""
    errors: list[str] = []
    subjects = clf.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        errors.append("'subjects' must be a non-empty list")
    else:
        invalid = [s for s in subjects if SE_SUBJECTS_SET and s not in SE_SUBJECTS_SET]
        if invalid:
            errors.append(f"unknown subjects: {invalid}")
    primary = clf.get("primary_subject")
    if not isinstance(primary, str) or not primary:
        errors.append("'primary_subject' must be a non-empty string")
    elif subjects and primary not in subjects:
        errors.append(f"primary_subject {primary!r} not in subjects list")
    confidence = clf.get("confidence")
    if confidence not in VALID_CONFIDENCE:
        errors.append(f"confidence must be one of {sorted(VALID_CONFIDENCE)}, got {confidence!r}")
    if errors:
        logger.warning("Skipping %s — validation errors: %s", stem, "; ".join(errors))
    return errors


def load_classifications(classifications_dir: Path) -> list[dict]:
    """Return valid classification records; log and skip invalid ones."""
    records = []
    invalid_count = 0
    for path in sorted(classifications_dir.glob("*.json")):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        clf = data.get("classification", {})
        if "error" in clf:
            logger.warning("Skipping file with error: %s", path.name)
            invalid_count += 1
            continue
        errors = _validate_classification(clf, path.stem)
        if errors:
            invalid_count += 1
            continue
        records.append(data)
    if invalid_count:
        logger.warning(
            "%d classification file(s) skipped due to errors.", invalid_count
        )
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
        help="Directory with classification JSON files (default: out/classifications/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory to save output files (default: out/analysis/)",
    )
    parser.add_argument(
        "--title",
        default="SE Knowledge Areas in QSE Literature",
        help='Histogram title (default: "SE Knowledge Areas in QSE Literature")',
    )
    parser.add_argument(
        "--hide-empty",
        action="store_true",
        help="Hide SWEBOK areas with zero papers from the histogram (default: show all).",
    )
    parser.add_argument(
        "--min-confidence",
        choices=["high", "medium", "low"],
        default=None,
        help="Exclude papers below this confidence threshold from the histogram.",
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

    # Apply confidence filter
    conf_rank = {"high": 3, "medium": 2, "low": 1}
    if args.min_confidence:
        min_rank = conf_rank[args.min_confidence]
        before = len(records)
        records = [
            r for r in records
            if conf_rank.get(r["classification"].get("confidence", "low"), 0) >= min_rank
        ]
        logger.info(
            "Confidence filter (%s+): kept %d / %d records.",
            args.min_confidence, len(records), before,
        )

    # -----------------------------------------------------------------------
    # Aggregate
    # -----------------------------------------------------------------------
    subject_counter: Counter = Counter()
    paper_rows = []
    # co-occurrence matrix: cooc[s1][s2] = number of papers listing both
    cooc: dict[str, Counter] = {s: Counter() for s in SE_SUBJECTS}

    for item in records:
        clf = item.get("classification", {})
        subjects: list[str] = clf.get("subjects", [])

        for subject in subjects:
            subject_counter[subject] += 1

        # Build co-occurrence (symmetric)
        for i, s1 in enumerate(subjects):
            for s2 in subjects[i + 1:]:
                if s1 in cooc and s2 in cooc:
                    cooc[s1][s2] += 1
                    cooc[s2][s1] += 1

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
    # Save frequency JSON (always include all 15 SWEBOK areas)
    # -----------------------------------------------------------------------
    all_subjects = SE_SUBJECTS if SE_SUBJECTS else list(subject_counter.keys())
    freq_data = [
        {"subject": s, "count": subject_counter.get(s, 0)}
        for s in all_subjects
    ]
    # Sort by count desc, then alpha
    freq_data.sort(key=lambda x: (-x["count"], x["subject"]))

    freq_path = args.output_dir / "subject_frequencies.json"
    with open(freq_path, "w", encoding="utf-8") as fh:
        json.dump(freq_data, fh, indent=2)
    logger.info("Saved frequency table to %s", freq_path)

    # -----------------------------------------------------------------------
    # Generate histogram
    # -----------------------------------------------------------------------
    plot_data = freq_data if not args.hide_empty else [e for e in freq_data if e["count"] > 0]

    if plot_data:
        subjects_plot = [entry["subject"] for entry in plot_data]
        counts_plot = [entry["count"] for entry in plot_data]
        colors = ["steelblue" if c > 0 else "#cccccc" for c in counts_plot]

        fig, ax = plt.subplots(
            figsize=(12, max(6, len(subjects_plot) * BAR_HEIGHT_FACTOR))
        )
        bars = ax.barh(subjects_plot, counts_plot, color=colors)
        ax.bar_label(bars, padding=4, fontsize=9)
        ax.set_xlabel("Number of Papers")
        ax.set_title(args.title)
        ax.invert_yaxis()
        max_count = max(counts_plot) if any(c > 0 for c in counts_plot) else 1
        ax.set_xlim(right=max_count * LABEL_PADDING_FACTOR)
        plt.tight_layout()

        histogram_path = args.output_dir / "histogram.png"
        plt.savefig(histogram_path, dpi=150, bbox_inches="tight")
        plt.close()
        logger.info("Saved histogram to %s", histogram_path)

    # -----------------------------------------------------------------------
    # Generate co-occurrence heatmap
    # -----------------------------------------------------------------------
    if SE_SUBJECTS and len(records) > 1:
        # Build matrix
        matrix = [[cooc[r].get(c, 0) for c in SE_SUBJECTS] for r in SE_SUBJECTS]

        # Save JSON
        cooc_json = {
            s1: {s2: cooc[s1].get(s2, 0) for s2 in SE_SUBJECTS}
            for s1 in SE_SUBJECTS
        }
        cooc_path = args.output_dir / "cooccurrence.json"
        with open(cooc_path, "w", encoding="utf-8") as fh:
            json.dump(cooc_json, fh, indent=2)
        logger.info("Saved co-occurrence matrix to %s", cooc_path)

        # Only render PNG if there's at least one non-zero co-occurrence
        if any(matrix[i][j] for i in range(len(SE_SUBJECTS)) for j in range(len(SE_SUBJECTS))):
            try:
                import numpy as np  # type: ignore[import]

                fig, ax = plt.subplots(figsize=(12, 10))
                im = ax.imshow(np.array(matrix), cmap="Blues", aspect="auto")
                ax.set_xticks(range(len(SE_SUBJECTS)))
                ax.set_yticks(range(len(SE_SUBJECTS)))
                short_labels = [s.replace("Software ", "") for s in SE_SUBJECTS]
                ax.set_xticklabels(short_labels, rotation=45, ha="right", fontsize=8)
                ax.set_yticklabels(short_labels, fontsize=8)
                plt.colorbar(im, ax=ax, label="Papers")
                ax.set_title("SE Knowledge Area Co-occurrence")
                plt.tight_layout()
                cooc_png = args.output_dir / "cooccurrence.png"
                plt.savefig(cooc_png, dpi=150, bbox_inches="tight")
                plt.close()
                logger.info("Saved co-occurrence heatmap to %s", cooc_png)
            except ImportError:
                logger.warning("numpy not installed — skipping cooccurrence.png")

    # -----------------------------------------------------------------------
    # Print summary table to stdout
    # -----------------------------------------------------------------------
    logger.info("\n%-45s  %s", "SE Knowledge Area", "Papers")
    logger.info("-" * 55)
    for entry in freq_data:
        logger.info("  %-43s  %d", entry["subject"], entry["count"])


if __name__ == "__main__":
    main()
