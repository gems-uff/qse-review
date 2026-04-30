"""Step 2a – Generate classification prompt files (deterministic, no LLM).

For each JSON file produced by ``extract_text.py`` this script writes a
Markdown file to ``data/prompts/<stem>.md`` containing:
  - The text excerpt to classify (abstract or first ~1 500 words).
  - Explicit instructions and the exact JSON format expected as output.

These prompt files are consumed either by:
  - An agentic CLI (Claude Code, Copilot CLI, etc.) running in ``agent`` mode,
    where the agent reads each file, decides the classification, and writes the
    JSON result to ``data/classifications/<stem>.json``.
  - ``classify.py --mode api`` (unchanged behaviour), which ignores these files
    and calls the OpenAI API directly.

Design choices:
  - Fully deterministic – no API calls.
  - Idempotent – already-generated prompt files are skipped unless
    ``--overwrite`` is given.
  - The expected JSON output format embedded in each prompt exactly matches
    what ``visualize.py`` needs to read, so the agent can write the
    classification file directly without any post-processing.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from textwrap import dedent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------
EXTRACTED_DIR = Path("data/extracted")
PROMPTS_DIR = Path("data/prompts")

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


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

def _build_prompt(filename: str, stem: str, text: str) -> str:
    subjects_list = "\n".join(f"- {s}" for s in SE_SUBJECTS)
    output_path = f"data/classifications/{stem}.json"

    return dedent(f"""\
        # Classification Task – {filename}

        You are an expert in Software Engineering (SE) and Quantum Computing.
        Classify the following paper excerpt according to the SWEBOK (Software
        Engineering Body of Knowledge) 4th Edition knowledge areas.

        ## Available SE Knowledge Areas

        {subjects_list}

        ## Paper Excerpt

        **Filename:** `{filename}`

        ---

        {text}

        ---

        ## Instructions

        1. Read the excerpt above carefully.
        2. Identify which SE knowledge areas the paper addresses.
           - Include only areas that are **clearly** relevant.
           - A paper may address **multiple** areas.
        3. Pick the **primary** (most prominent) subject.
        4. Write a **one-sentence** summary of the SE contribution.
        5. Assign a **confidence** level: `high`, `medium`, or `low`.
        6. Write the result as a JSON file to:

           `{output_path}`

        ## Required Output Format

        The file `{output_path}` must contain **exactly** this JSON structure
        (no extra keys, no markdown fences – raw JSON only):

        ```json
        {{
          "filename": "{filename}",
          "stem": "{stem}",
          "classification": {{
            "subjects": ["<Subject1>", "<Subject2>"],
            "primary_subject": "<Most prominent subject>",
            "summary": "<One sentence describing the SE contribution>",
            "confidence": "high|medium|low"
          }}
        }}
        ```

        > **Note:** `subjects` values must come **verbatim** from the list of
        > Available SE Knowledge Areas above.  Do not invent new subjects.
    """)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Markdown prompt files for each extracted paper so that "
            "an agentic CLI (Claude Code, Copilot CLI, etc.) can classify them "
            "without calling an external API."
        )
    )
    parser.add_argument(
        "--extracted-dir",
        type=Path,
        default=EXTRACTED_DIR,
        help="Directory with extracted paper JSON files (default: data/extracted/)",
    )
    parser.add_argument(
        "--prompts-dir",
        type=Path,
        default=PROMPTS_DIR,
        help="Directory to save prompt Markdown files (default: data/prompts/)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-generate prompt files that already exist.",
    )
    args = parser.parse_args(argv)

    if not args.extracted_dir.exists():
        logger.error("Extracted directory not found: %s", args.extracted_dir)
        sys.exit(1)

    args.prompts_dir.mkdir(parents=True, exist_ok=True)

    extracted_files = sorted(args.extracted_dir.glob("*.json"))
    if not extracted_files:
        logger.warning("No extracted JSON files found in %s", args.extracted_dir)
        sys.exit(0)

    logger.info("Found %d extracted file(s) to process.", len(extracted_files))

    success = errors = skipped = 0

    for extracted_path in extracted_files:
        prompt_path = args.prompts_dir / f"{extracted_path.stem}.md"

        if prompt_path.exists() and not args.overwrite:
            logger.info("  skip (already generated): %s", prompt_path.name)
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

        stem = extracted_path.stem
        filename = extracted_data["filename"]
        prompt = _build_prompt(filename, stem, text)

        with open(prompt_path, "w", encoding="utf-8") as fh:
            fh.write(prompt)

        logger.info("  generated: %s", prompt_path.name)
        success += 1

    logger.info(
        "Prompt generation complete — success: %d  errors: %d  skipped: %d",
        success,
        errors,
        skipped,
    )


if __name__ == "__main__":
    main()
