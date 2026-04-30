"""Step 2 – Classify SE subjects in each paper using an LLM.

Two operating modes are supported via ``--mode``:

``api`` (default)
    Calls the OpenAI API directly for each paper (original behaviour).
    Requires the ``OPENAI_API_KEY`` environment variable.

``agent``
    Designed to run inside an agentic CLI environment (Claude Code,
    Copilot CLI, etc.).  Instead of calling an API the script prints each
    prompt file from ``data/prompts/`` to **stdout** one at a time, then
    waits for the agent to write the corresponding classification JSON to
    ``data/classifications/<stem>.json`` before moving on to the next paper.

    The agent is responsible for reading the prompt, deciding the
    classification, and writing the JSON file in the exact format described
    in the prompt.  Run ``generate_prompts.py`` first to create the prompt
    files.

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
      - ``tokens_used``      – total tokens consumed for this call (api mode only)

Prerequisites:
  ``api`` mode: set the ``OPENAI_API_KEY`` environment variable before running.
  ``agent`` mode: run ``generate_prompts.py`` first.
"""

import argparse
import json
import logging
import os
import sys
import time
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
EXTRACTED_DIR = Path("data/extracted")
CLASSIFICATIONS_DIR = Path("data/classifications")
PROMPTS_DIR = Path("data/prompts")
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
    """Print each prompt to stdout so an agentic CLI can classify it.

    The agent is expected to:
      1. Read the prompt printed to stdout.
      2. Decide the classification.
      3. Write the JSON result to ``data/classifications/<stem>.json``
         in the exact format described inside the prompt.

    The script then polls for the output file before moving to the next paper,
    so the agent and this script stay in sync when the agent processes papers
    sequentially (which is the normal case for agentic CLIs).
    """
    if not args.prompts_dir.exists():
        logger.error(
            "Prompts directory not found: %s\n"
            "Run 'python scripts/generate_prompts.py' first.",
            args.prompts_dir,
        )
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    prompt_files = sorted(args.prompts_dir.glob("*.md"))
    if not prompt_files:
        logger.warning(
            "No prompt files found in %s. "
            "Run 'python scripts/generate_prompts.py' first.",
            args.prompts_dir,
        )
        sys.exit(0)

    logger.info(
        "Agent mode — %d prompt file(s) to process.", len(prompt_files)
    )

    success = skipped = 0

    for prompt_path in prompt_files:
        stem = prompt_path.stem
        output_path = args.output_dir / f"{stem}.json"

        if output_path.exists() and not args.overwrite:
            logger.info("  skip (already classified): %s", stem)
            skipped += 1
            continue

        # Print the prompt so the agent can read it and act on it.
        prompt_text = prompt_path.read_text(encoding="utf-8")
        print("\n" + "=" * 72)
        print(f"CLASSIFY: {stem}  ({success + skipped + 1}/{len(prompt_files)})")
        print("=" * 72)
        print(prompt_text)
        print("=" * 72)
        print(
            f"\n[classify.py] Waiting for the agent to write: {output_path}\n"
            "[classify.py] Please create that file now, then press Enter to continue."
        )
        sys.stdout.flush()

        # Wait for the agent to create the output file.
        input()

        if output_path.exists():
            logger.info("  classified: %s", stem)
            success += 1
        else:
            logger.warning(
                "  output file not found after confirmation: %s", output_path
            )

    logger.info(
        "Agent classification complete — success: %d  skipped: %d",
        success,
        skipped,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Classify SE subjects in extracted paper texts using an LLM.\n\n"
            "Modes:\n"
            "  api   – call OpenAI API directly (requires OPENAI_API_KEY)\n"
            "  agent – print prompts to stdout for an agentic CLI to classify\n"
            "          (requires 'generate_prompts.py' to have been run first)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["api", "agent"],
        default="api",
        help="Classification mode: 'api' (default) or 'agent'.",
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
        "--prompts-dir",
        type=Path,
        default=PROMPTS_DIR,
        help="Directory with prompt Markdown files, used in agent mode (default: data/prompts/)",
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
