#!/usr/bin/env bash
# run_pipeline.sh – convenience script to run the full QSE classification pipeline.
#
# Usage:
#   export OPENAI_API_KEY="sk-..."
#   bash run_pipeline.sh [--overwrite]
#
# Pass --overwrite to force re-extraction and re-classification of all papers.

set -euo pipefail

OVERWRITE_FLAG=""
if [[ "${1:-}" == "--overwrite" ]]; then
  OVERWRITE_FLAG="--overwrite"
fi

echo "=================================================="
echo " QSE Paper Classification Pipeline"
echo "=================================================="

echo ""
echo "Step 1/3 — Extracting text from PDFs in papers/ ..."
python scripts/extract_text.py $OVERWRITE_FLAG

echo ""
echo "Step 2/3 — Classifying SE subjects with LLM ..."
python scripts/classify.py $OVERWRITE_FLAG

echo ""
echo "Step 3/3 — Generating histogram ..."
python scripts/visualize.py

echo ""
echo "=================================================="
echo " Done! Results saved to data/output/"
echo "   histogram.png        – bar chart"
echo "   paper_subjects.csv   – per-paper table"
echo "   subject_frequencies.json – aggregate counts"
echo "=================================================="
