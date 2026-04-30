#!/usr/bin/env bash
# run_pipeline.sh – convenience script to run the full QSE classification pipeline.
#
# Usage:
#   bash run_pipeline.sh [--overwrite] [--agent]
#
#   --overwrite   Force re-extraction and re-classification of all papers.
#   --agent       Run in agent mode: the calling agent (Claude Code, Copilot
#                 CLI, etc.) classifies papers using its own intelligence instead
#                 of calling the OpenAI API.  No OPENAI_API_KEY is needed.
#                 In this mode the script generates prompt files and then calls
#                 classify.py --mode agent, which prints each prompt to stdout
#                 and waits for the agent to write the classification JSON.
#                 See AGENT_INSTRUCTIONS.md for full details.
#
#   Without --agent:
#     export OPENAI_API_KEY="sk-..."
#     bash run_pipeline.sh [--overwrite]

set -euo pipefail

OVERWRITE_FLAG=""
AGENT_MODE=false

for arg in "$@"; do
  case "$arg" in
    --overwrite) OVERWRITE_FLAG="--overwrite" ;;
    --agent)     AGENT_MODE=true ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: bash run_pipeline.sh [--overwrite] [--agent]" >&2
      exit 1
      ;;
  esac
done

echo "=================================================="
echo " QSE Paper Classification Pipeline"
if $AGENT_MODE; then
  echo " Mode: AGENT (no API key required)"
else
  echo " Mode: API  (requires OPENAI_API_KEY)"
fi
echo "=================================================="

echo ""
echo "Step 1/3 — Extracting text from PDFs in papers/ ..."
python scripts/extract_text.py $OVERWRITE_FLAG

if $AGENT_MODE; then
  echo ""
  echo "Step 2a/3 — Generating prompt files for agent classification ..."
  python scripts/generate_prompts.py $OVERWRITE_FLAG

  echo ""
  echo "Step 2b/3 — Agent classification mode"
  echo "  The script will print each prompt and wait for you to write the"
  echo "  classification JSON to data/classifications/<stem>.json."
  echo "  See AGENT_INSTRUCTIONS.md for the required JSON format."
  echo ""
  python scripts/classify.py --mode agent $OVERWRITE_FLAG
else
  echo ""
  echo "Step 2/3 — Classifying SE subjects with LLM (API mode) ..."
  python scripts/classify.py --mode api $OVERWRITE_FLAG
fi

echo ""
echo "Step 3/3 — Generating histogram ..."
python scripts/visualize.py

echo ""
echo "=================================================="
echo " Done! Results saved to data/output/"
echo "   histogram.png              – bar chart"
echo "   paper_subjects.csv         – per-paper table"
echo "   subject_frequencies.json   – aggregate counts"
echo "=================================================="

