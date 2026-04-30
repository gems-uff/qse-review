# Agent Instructions – QSE Paper Classification Pipeline

This document tells an agentic CLI environment (Claude Code, GitHub Copilot
CLI, or any similar tool) exactly how to run the QSE classification pipeline
in **agent mode**, where *the agent itself* performs the classification step
instead of delegating it to an external API.

---

## Why agent mode?

| Aspect | API mode (`--mode api`) | Agent mode (`--mode agent`) |
|---|---|---|
| Model | gpt-4o-mini (configurable) | Whatever model the agent uses (e.g. Claude Opus, GPT-4.1) |
| Cost | Paid per token | Covered by your Copilot Pro / Claude Pro subscription |
| Quality | Good | Excellent – frontier models classify more accurately |
| Setup | `OPENAI_API_KEY` env var required | No API key needed |

---

## Pipeline overview

```
papers/*.pdf
    │
    ▼  Step 1 – deterministic (pdfplumber)
data/extracted/*.json
    │
    ▼  Step 2a – deterministic (generate_prompts.py)
data/prompts/*.md
    │
    ▼  Step 2b – YOU (the agent) read each prompt, classify, write JSON
data/classifications/*.json
    │
    ▼  Step 3 – deterministic (visualize.py)
data/output/{histogram.png, paper_subjects.csv, subject_frequencies.json}
```

---

## Step-by-step instructions

### Step 1 – Extract text from PDFs

```bash
python scripts/extract_text.py [--overwrite]
```

Reads every PDF in `papers/` and writes a JSON file to `data/extracted/`.
Pass `--overwrite` to re-extract already-processed papers.

### Step 2a – Generate prompt files

```bash
python scripts/generate_prompts.py [--overwrite]
```

Reads every JSON in `data/extracted/` and writes a Markdown prompt file to
`data/prompts/<stem>.md`.  These files describe the classification task and
specify the exact JSON format you must produce.

### Step 2b – Classify papers (YOUR task as the agent)

For **each file** in `data/prompts/`:

1. Read the file (e.g. `data/prompts/my_paper.md`).
2. Classify the paper excerpt according to the SWEBOK knowledge areas listed
   in the prompt.
3. Write the result to `data/classifications/<stem>.json` using the **exact**
   JSON format shown below and repeated inside each prompt.

You may process the files in any order.  Already-classified papers (i.e.
files that already exist in `data/classifications/`) can be skipped unless
you want to re-classify them.

Alternatively, you can run `classify.py --mode agent` and the script will
print each prompt to stdout one at a time, pause, and wait for you to create
the output file before moving to the next paper:

```bash
python scripts/classify.py --mode agent [--overwrite]
```

### Step 3 – Generate histogram

```bash
python scripts/visualize.py
```

Reads all JSON files from `data/classifications/` and writes:
- `data/output/histogram.png` – horizontal bar chart
- `data/output/paper_subjects.csv` – per-paper table
- `data/output/subject_frequencies.json` – aggregate counts

---

## Required JSON output format

Each file you write to `data/classifications/<stem>.json` must contain
**exactly** this structure (raw JSON, no markdown code fences):

```json
{
  "filename": "<original PDF filename, e.g. my_paper.pdf>",
  "stem": "<filename without extension, e.g. my_paper>",
  "classification": {
    "subjects": ["<Subject1>", "<Subject2>"],
    "primary_subject": "<Most prominent subject>",
    "summary": "<One sentence describing the SE contribution of the paper>",
    "confidence": "high|medium|low"
  }
}
```

### Valid `subjects` values (SWEBOK 4th Edition)

Use these strings **verbatim** – `visualize.py` matches on exact text:

- Software Requirements
- Software Architecture
- Software Design
- Software Construction
- Software Testing
- Software Maintenance
- Software Configuration Management
- Software Engineering Management
- Software Engineering Process
- Software Engineering Models and Methods
- Software Quality
- Software Engineering Professional Practice
- Software Engineering Economics
- Software Security
- Software Safety

---

## Quick reference

```bash
# Full pipeline in agent mode (no API key needed)
python scripts/extract_text.py
python scripts/generate_prompts.py
# ← you (the agent) classify papers in data/prompts/ → data/classifications/
python scripts/visualize.py

# Or use run_pipeline.sh:
bash run_pipeline.sh --agent
```

---

## Tips

- **Idempotency**: all scripts skip already-processed files by default.
  Pass `--overwrite` to re-run a step.
- **Partial runs**: if you stop mid-way, simply restart; already-written
  classification files are preserved.
- **Confidence levels**: use `"high"` when the paper clearly focuses on the
  area, `"medium"` when there is partial overlap, `"low"` when the mapping is
  uncertain.
