"""Step 2 – Classify SE subjects in each paper using an LLM (OpenAI).

For each JSON file produced by ``extract_text.py`` the script sends the
``text_for_classification`` field (abstract or first ~1 500 words) to an
OpenAI chat model and asks it to label the paper with one or more
**SWEBOK knowledge areas**.

Design choices to minimise token consumption:
  - Only the short ``text_for_classification`` excerpt is sent (not the full
    paper text).
  - ``temperature=0`` makes responses deterministic / cacheable.
  - Already-classified papers are skipped unless ``--overwrite`` is given.
  - A configurable ``--delay`` avoids rate-limit errors.

The classification JSON written per paper:
  - ``filename``        – original PDF file name
  - ``stem``            – paper identifier (PDF stem)
  - ``classification``  – LLM output dict:
      - ``subjects``         – list of matched SWEBOK knowledge areas
      - ``primary_subject``  – most prominent area
      - ``summary``          – one-sentence SE contribution summary
      - ``confidence``       – "high" | "medium" | "low"
      - ``tokens_used``      – total tokens consumed for this call

Prerequisites:
  Set the ``OPENAI_API_KEY`` environment variable before running.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------
EXTRACTED_DIR = Path("data/extracted")
CLASSIFICATIONS_DIR = Path("data/classifications")
DEFAULT_MODEL = "gpt-4o-mini"

# SWEBOK 4th Edition Knowledge Areas used as the classification taxonomy
SE_SUBJECTS: list[str] = [
    "Software Requirements",
    "Software Architecture",
    "Software Design",
    "Software Construction",
    "Software Testing",
    "Software Maintenance",
    "Software Configuration Management",
    "Software Engineering Management",
    "Software Engineering Process",
    "Software Engineering Models and Methods",
    "Software Quality",
    "Software Engineering Professional Practice",
    "Software Engineering Economics",
    "Software Security",
    "Software Safety",
]

_SYSTEM_PROMPT = """\
You are an expert in Software Engineering (SE) and Quantum Computing.
Your task is to classify scientific papers on Quantum Software Engineering (QSE)
according to the Software Engineering Knowledge Areas from SWEBOK
(Software Engineering Body of Knowledge), 4th Edition.

For each paper, identify which SE knowledge areas it addresses based on the
provided text excerpt. A paper may address multiple areas; only include areas
that are clearly relevant.

Respond ONLY with a JSON object in exactly this format (no extra keys):
{
  "subjects": ["<Subject1>", "<Subject2>"],
  "primary_subject": "<Most prominent subject>",
  "summary": "<One sentence describing the SE contribution of the paper>",
  "confidence": "high|medium|low"
}
"""


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def classify_paper(client: OpenAI, model: str, text: str, filename: str) -> dict:
    """Return classification dict for a single paper excerpt."""
    subjects_list = "\n".join(f"- {s}" for s in SE_SUBJECTS)

    user_prompt = (
        f"Classify the following paper excerpt according to SE knowledge areas.\n\n"
        f"Available SE Knowledge Areas:\n{subjects_list}\n\n"
        f"Paper filename: {filename}\n\n"
        f"---\n{text}\n---\n\n"
        f"Return a JSON object with the fields: subjects, primary_subject, "
        f"summary, confidence."
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    result: dict = json.loads(response.choices[0].message.content)
    result["tokens_used"] = response.usage.total_tokens
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Classify SE subjects in extracted paper texts using an LLM. "
            "Set OPENAI_API_KEY in the environment before running."
        )
    )
    parser.add_argument(
        "--extracted-dir",
        type=Path,
        default=EXTRACTED_DIR,
        help="Directory with extracted paper JSON files (default: data/extracted/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=CLASSIFICATIONS_DIR,
        help="Directory to save classification JSON files (default: data/classifications/)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenAI model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-classify papers that already have a classification file.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds to wait between API calls to respect rate limits (default: 0.5).",
    )
    args = parser.parse_args(argv)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY environment variable is not set.")
        sys.exit(1)

    if not args.extracted_dir.exists():
        logger.error("Extracted directory not found: %s", args.extracted_dir)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    extracted_files = sorted(args.extracted_dir.glob("*.json"))
    if not extracted_files:
        logger.warning("No extracted JSON files found in %s", args.extracted_dir)
        sys.exit(0)

    logger.info("Found %d extracted file(s) to classify.", len(extracted_files))

    client = OpenAI(api_key=api_key)

    success = errors = skipped = 0
    total_tokens = 0

    for extracted_path in extracted_files:
        output_path = args.output_dir / extracted_path.name

        if output_path.exists() and not args.overwrite:
            logger.info("  skip (already classified): %s", extracted_path.name)
            skipped += 1
            continue

        with open(extracted_path, encoding="utf-8") as fh:
            extracted_data = json.load(fh)

        if extracted_data.get("error"):
            logger.warning(
                "  skip (extraction error): %s", extracted_path.name
            )
            errors += 1
            continue

        text = extracted_data.get("text_for_classification", "")
        if not text.strip():
            logger.warning("  skip (no text available): %s", extracted_path.name)
            errors += 1
            continue

        logger.info("  classifying: %s", extracted_data["filename"])

        try:
            classification = classify_paper(
                client,
                args.model,
                text,
                extracted_data["filename"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("  LLM error for %s: %s", extracted_path.name, exc)
            classification = {"error": str(exc)}
            errors += 1
        else:
            success += 1
            total_tokens += classification.get("tokens_used", 0)

        output_data = {
            "filename": extracted_data["filename"],
            "stem": extracted_path.stem,
            "classification": classification,
        }

        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(output_data, fh, ensure_ascii=False, indent=2)

        time.sleep(args.delay)

    logger.info(
        "Classification complete — success: %d  errors: %d  skipped: %d  "
        "total tokens used: %d",
        success,
        errors,
        skipped,
        total_tokens,
    )


if __name__ == "__main__":
    main()
