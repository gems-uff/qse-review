"""Classify SE subjects in each paper.

Two operating modes are supported via ``--mode``:

``agent`` (default)
    Prints a status report listing every extracted paper that has not yet been
    classified, together with a short excerpt of its text.  This gives an
    agentic CLI (Claude Code, Copilot CLI, etc.) quick visibility into what
    work remains.  The agent then reads each ``out/extracted/<stem>.json``
    file, decides the classification, and writes the result to
    ``out/classifications/<stem>.json`` following the instructions in
    ``CLAUDE.md``.

``api``
    Calls the OpenAI API directly for each unclassified paper.
    Requires the ``OPENAI_API_KEY`` environment variable.

The classification JSON written per paper:
  - ``filename``        – original PDF file name
  - ``stem``            – paper identifier (PDF stem)
  - ``classification``  – classification dict:
      - ``subjects``         – list of matched SWEBOK knowledge areas
      - ``primary_subject``  – most prominent area
      - ``summary``          – one-sentence SE contribution summary
      - ``confidence``       – "high" | "medium" | "low"
      - ``tokens_used``      – total tokens consumed (api mode only)
  - ``metadata``        – provenance dict:
      - ``classified_at``    – ISO-8601 timestamp
      - ``classifier``       – "agent" or "api"
      - ``model``            – model name (api mode only)

Prerequisites:
  ``agent`` mode: run ``extract_text.py`` first.
  ``api`` mode: run ``extract_text.py`` first and set ``OPENAI_API_KEY``.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover – optional in agent mode
    OpenAI = None  # type: ignore[assignment,misc]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------
EXTRACTED_DIR = Path("out/extracted")
CLASSIFICATIONS_DIR = Path("out/classifications")
SWEBOK_SUBJECTS_PATH = Path("swebok_subjects.json")
DEFAULT_MODEL = "gpt-4o-mini"
VALID_CONFIDENCE = {"high", "medium", "low"}


def _load_se_subjects() -> list[str]:
    """Load SWEBOK knowledge areas from the canonical JSON file."""
    if SWEBOK_SUBJECTS_PATH.exists():
        with open(SWEBOK_SUBJECTS_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    # Fallback: inline list (kept in sync via tests)
    return [
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


SE_SUBJECTS: list[str] = _load_se_subjects()
SE_SUBJECTS_SET: set[str] = set(SE_SUBJECTS)

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
# Validation
# ---------------------------------------------------------------------------

def _validate_classification(clf: dict, stem: str) -> list[str]:
    """Return a list of validation error strings (empty = valid)."""
    errors: list[str] = []
    subjects = clf.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        errors.append("'subjects' must be a non-empty list")
    else:
        invalid = [s for s in subjects if s not in SE_SUBJECTS_SET]
        if invalid:
            errors.append(f"unknown subjects: {invalid}")

    primary = clf.get("primary_subject")
    if not isinstance(primary, str) or not primary:
        errors.append("'primary_subject' must be a non-empty string")
    elif subjects and primary not in subjects:
        errors.append(f"'primary_subject' ({primary!r}) not in subjects list")

    confidence = clf.get("confidence")
    if confidence not in VALID_CONFIDENCE:
        errors.append(f"'confidence' must be one of {sorted(VALID_CONFIDENCE)}, got {confidence!r}")

    if errors:
        logger.warning("Validation errors in %s: %s", stem, "; ".join(errors))
    return errors


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def classify_paper(client: "OpenAI", model: str, text: str, filename: str) -> dict:
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

    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
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

            errors = _validate_classification(result, filename)
            if errors:
                result["validation_errors"] = errors

            return result
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < 3:
                wait = 2 ** attempt
                logger.warning(
                    "  API error (attempt %d/3) for %s: %s — retrying in %ds",
                    attempt, filename, exc, wait,
                )
                time.sleep(wait)

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_api_mode(args: argparse.Namespace) -> None:
    """Classify papers by calling the OpenAI API directly."""
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

    if OpenAI is None:
        logger.error(
            "The 'openai' package is not installed. "
            "Run: pip install openai"
        )
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    try:
        from tqdm import tqdm  # type: ignore[import]
        iterator = tqdm(extracted_files, desc="Classifying", unit="paper")
    except ImportError:
        iterator = extracted_files  # type: ignore[assignment]

    success = errors = skipped = 0
    total_tokens = 0

    for extracted_path in iterator:
        output_path = args.output_dir / extracted_path.name

        if output_path.exists() and not args.overwrite:
            logger.info("  skip (already classified): %s", extracted_path.name)
            skipped += 1
            continue

        with open(extracted_path, encoding="utf-8") as fh:
            extracted_data = json.load(fh)

        if extracted_data.get("error"):
            logger.warning("  skip (extraction error): %s", extracted_path.name)
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
            "metadata": {
                "classified_at": datetime.now(timezone.utc).isoformat(),
                "classifier": "api",
                "model": args.model,
            },
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


def _run_agent_mode(args: argparse.Namespace) -> None:
    """Print a status report of papers that still need classification.

    This gives an agentic CLI quick visibility into what work remains so it
    can proceed to classify each ``out/extracted/<stem>.json`` and write the
    result to ``out/classifications/<stem>.json`` following ``CLAUDE.md``.
    """
    if not args.extracted_dir.exists():
        logger.error("Extracted directory not found: %s", args.extracted_dir)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    extracted_files = sorted(args.extracted_dir.glob("*.json"))
    if not extracted_files:
        logger.warning("No extracted JSON files found in %s", args.extracted_dir)
        sys.exit(0)

    _EXCERPT_LEN = 300  # characters to show per paper

    pending = []
    done = []

    for extracted_path in extracted_files:
        output_path = args.output_dir / extracted_path.name
        if output_path.exists() and not args.overwrite:
            # Validate existing classification and warn on issues
            with open(output_path, encoding="utf-8") as fh:
                data = json.load(fh)
            clf = data.get("classification", {})
            if "error" not in clf:
                errs = _validate_classification(clf, extracted_path.stem)
                if errs:
                    logger.warning(
                        "  existing classification has errors (%s): %s",
                        extracted_path.stem,
                        "; ".join(errs),
                    )
            done.append(extracted_path.stem)
            continue

        with open(extracted_path, encoding="utf-8") as fh:
            data = json.load(fh)

        if data.get("error"):
            logger.warning("  skip (extraction error): %s", extracted_path.name)
            continue

        excerpt = data.get("text_for_classification", "").strip()
        pending.append(
            {
                "stem": extracted_path.stem,
                "filename": data.get("filename", extracted_path.name),
                "excerpt": excerpt[:_EXCERPT_LEN] + ("…" if len(excerpt) > _EXCERPT_LEN else ""),
                "output_path": str(output_path),
            }
        )

    print(f"\n{'=' * 72}")
    print(f"QSE Classification Status — {len(done)} done, {len(pending)} pending")
    print(f"{'=' * 72}\n")

    if not pending:
        print("✓ All papers have been classified.")
        print(f"  Run `python scripts/visualize.py` to generate the histogram.\n")
        return

    for i, paper in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] {paper['filename']}")
        print(f"  Output : {paper['output_path']}")
        print(f"  Excerpt: {paper['excerpt']}")
        print()

    print(
        "Read CLAUDE.md for the required JSON format, then write each\n"
        "out/classifications/<stem>.json file and re-run this command to\n"
        "check progress.\n"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Classify SE subjects in extracted paper texts.\n\n"
            "Modes:\n"
            "  agent (default) – print a status report of unclassified papers\n"
            "                    so an agentic CLI knows what work remains\n"
            "  api             – call OpenAI API directly (requires OPENAI_API_KEY)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["api", "agent"],
        default="agent",
        help="Classification mode: 'agent' (default) or 'api'.",
    )
    parser.add_argument(
        "--extracted-dir",
        type=Path,
        default=EXTRACTED_DIR,
        help="Directory with extracted paper JSON files (default: out/extracted/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=CLASSIFICATIONS_DIR,
        help="Directory to save classification JSON files (default: out/classifications/)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenAI model to use in api mode (default: {DEFAULT_MODEL})",
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
        help="Seconds to wait between API calls in api mode (default: 0.5).",
    )
    args = parser.parse_args(argv)

    if args.mode == "agent":
        _run_agent_mode(args)
    else:
        _run_api_mode(args)


if __name__ == "__main__":
    main()
