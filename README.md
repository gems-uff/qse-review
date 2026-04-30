# qse-review

Automated pipeline to classify scientific articles on **Quantum Software Engineering (QSE)**
according to the SE knowledge areas defined in [SWEBOK 4th Edition](https://www.computer.org/education/bodies-of-knowledge/software-engineering).
The pipeline produces a frequency histogram and co-occurrence heatmap showing which SE topics
are most covered in the QSE literature.

**Step 2 (classification) must be performed by an AI agent** — the agent reads `CLAUDE.md`,
reasons about each paper, and writes the classification files.  No API key is needed if you
use Claude Code or GitHub Copilot.  An OpenAI API fallback is also available.

---

## Pipeline overview

```
papers/QSE - Papers.xlsx          ← spreadsheet with DOI links (gitignored)
    │
    ▼ Step 0 – resolve_dois.py      (you run this; resolves DOIs via CrossRef/DBLP)
out/dois.json                     ← canonical paper list with DOIs
out/unresolved_papers.json        ← papers that could not be resolved (manual follow-up)
    │
    ▼ Step 1 – extract_text.py      (you run this; deterministic, no LLM)
out/extracted/*.json
    │
    ▼ Step 2 – AI agent classifies  (agent runs this; requires an agentic CLI)
out/classifications/*.json
    │
    ▼ Step 3 – visualize.py         (you run this; deterministic, no LLM)
out/analysis/
  ├── histogram.png
  ├── cooccurrence.png
  ├── paper_subjects.csv
  ├── subject_frequencies.json
  └── cooccurrence.json
```

---

## Quick start with Claude Code (recommended)

This is the recommended way to run the pipeline.  Claude Code acts as the agent
for Step 2 and can also drive Steps 1 and 3 on your behalf.

### Prerequisites

1. Install [Claude Code](https://claude.ai/code) and log in.
2. Set up a Python environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Step 1 — Copy the spreadsheet

Place `QSE - Papers.xlsx` inside the `papers/` directory (gitignored):

```
papers/
└── QSE - Papers.xlsx
```

Then resolve DOIs:

```bash
python scripts/resolve_dois.py --mailto your@email.com
```

This creates `out/dois.json` and `out/unresolved_papers.json`.  The script is
incremental: re-running it after adding papers to the spreadsheet only processes
the new entries.

### Step 2 — Open Claude Code in the project directory

```bash
cd qse-review
claude
```

Claude Code automatically reads `CLAUDE.md`, which contains all the instructions
the agent needs to run the pipeline.

### Step 3 — Choose a model

Inside Claude Code, set the model with the `/model` command.  For classification
tasks that require careful reading and reasoning about scientific papers, **Opus**
gives the best results:

```
/model opus     # highest quality — recommended for final runs
/model sonnet   # faster and cheaper — good for testing with a small corpus
```

You can also set the effort level (controls how much the model "thinks" before
answering):

```
/effort high    # recommended for classification tasks
```

### Step 4 — Start the pipeline with a single prompt

Type the following prompt and press Enter:

```
Execute the QSE classification pipeline following the instructions in CLAUDE.md.
```

Claude Code will then:
1. Run `python scripts/extract_text.py` to extract text from all PDFs in `papers/`.
2. Read each `out/extracted/<paper>.json` and classify it against the 15 SWEBOK knowledge areas.
3. Write one `out/classifications/<paper>.json` per paper.
4. Run `python scripts/visualize.py` to generate the histogram and co-occurrence charts.

> **Tip:** the pipeline is idempotent — already-classified papers are skipped automatically.
> If you add more PDFs later, just run the same prompt again and only the new papers will
> be processed.

### Step 5 — Inspect the results

Open the files in `out/analysis/`:

| File | What it shows |
|------|---------------|
| `histogram.png` | How many papers address each of the 15 SWEBOK areas |
| `cooccurrence.png` | Which pairs of areas tend to appear together |
| `paper_subjects.csv` | Per-paper breakdown with summary and confidence |
| `subject_frequencies.json` | Raw counts (for further analysis) |

---

## Quick start with other agentic tools

The same `CLAUDE.md` instructions work with any agentic CLI that can read files
and execute shell commands.

### GitHub Copilot (VS Code Agent mode)

1. Open the `qse-review` folder in VS Code.
2. Open the Copilot Chat panel and switch to **Agent** mode (`@workspace`).
3. Enter the prompt:
   ```
   Execute the QSE classification pipeline following the instructions in CLAUDE.md.
   ```

### OpenAI API (fully automated, no interactive agent)

If you prefer a non-interactive run using the OpenAI API directly:

```bash
export OPENAI_API_KEY="sk-..."
python scripts/extract_text.py
python scripts/classify.py --mode api [--model gpt-4o] [--delay 0.5]
python scripts/visualize.py
```

See the [API mode reference](#api-mode-reference) section below for all flags.

---

## Running the steps manually

You can also run each step individually without an agent.

### Step 1 — Extract text from PDFs

```bash
python scripts/extract_text.py [--papers-dir papers/] [--output-dir out/extracted/] [--overwrite]
```

Reads every `*.pdf` in `papers/`, extracts text (up to 10 pages), and attempts
to isolate the abstract.  Pass `--ocr` (or install `tesseract` to auto-enable it)
for scanned / image-only PDFs:

```bash
# macOS: brew install tesseract poppler && pip install pdf2image pytesseract
python scripts/extract_text.py --ocr
```

### Step 2 — Check classification status (agent mode)

At any time, check which papers still need classifying:

```bash
python scripts/classify.py
```

This prints a status report.  The agent uses it to know what work remains
and then writes the classification files itself.

### Step 3 — Generate visualisations

```bash
python scripts/visualize.py [--classifications-dir out/classifications/] \
    [--output-dir out/analysis/] [--title "My Title"] \
    [--hide-empty] [--min-confidence high|medium|low]
```

`--hide-empty` omits SWEBOK areas with zero papers (by default all 15 are shown,
so gaps are visible).  `--min-confidence` filters out low-confidence classifications
before counting.

---

## API mode reference

| Flag | Default | Description |
|------|---------|-------------|
| `--mode` | `agent` | `agent` (status report) or `api` (call OpenAI) |
| `--model` | `gpt-4o-mini` | OpenAI model (`gpt-4o` for higher accuracy) |
| `--delay` | `0.5` | Seconds between API calls (rate-limit buffer) |
| `--overwrite` | off | Re-classify papers that already have a result |

Token cost estimate: ~500–700 tokens per paper with `gpt-4o-mini`.
500 papers ≈ $0.20–$0.35 USD (as of 2025).

---

## SE Knowledge Areas (taxonomy)

The taxonomy follows **SWEBOK 4th Edition** and is defined in `swebok_subjects.json`:

| # | Knowledge Area |
|---|----------------|
| 1 | Software Requirements |
| 2 | Software Architecture |
| 3 | Software Design |
| 4 | Software Construction |
| 5 | Software Testing |
| 6 | Software Maintenance |
| 7 | Software Configuration Management |
| 8 | Software Engineering Management |
| 9 | Software Engineering Process |
| 10 | Software Engineering Models and Methods |
| 11 | Software Quality |
| 12 | Software Engineering Professional Practice |
| 13 | Software Engineering Economics |
| 14 | Software Security |
| 15 | Software Safety |

---

## Classification JSON format

Each file in `out/classifications/<stem>.json`:

```json
{
  "filename": "my_paper.pdf",
  "stem": "my_paper",
  "classification": {
    "subjects": ["Software Testing", "Software Quality"],
    "primary_subject": "Software Testing",
    "summary": "One sentence describing the SE contribution of the paper.",
    "confidence": "high"
  },
  "metadata": {
    "classified_at": "2025-01-01T00:00:00+00:00",
    "classifier": "agent",
    "model": null
  }
}
```

`confidence`: `"high"` = paper clearly focuses on the area; `"medium"` = partial
overlap; `"low"` = uncertain mapping.

---

## Repository structure

```
qse-review/
├── papers/                  ← place QSE - Papers.xlsx here (gitignored)
├── out/                     ← all pipeline output (gitignored; auto-created)
│   ├── dois.json            ← Step 0 output: canonical paper list with DOIs
│   ├── unresolved_papers.json ← Step 0 output: papers without a DOI
│   ├── extracted/           ← Step 1 output
│   ├── classifications/     ← Step 2 output
│   └── analysis/            ← Step 3 output
├── tests/
│   ├── fixtures/
│   │   └── papers/          ← sample PDFs used by the test suite
│   └── test_pipeline.py
├── scripts/
│   ├── resolve_dois.py      ← Step 0 script (spreadsheet → DOI list)
│   ├── extract_text.py      ← Step 1 script
│   ├── classify.py          ← Step 2 status / API helper
│   └── visualize.py         ← Step 3 script
├── swebok_subjects.json     ← canonical SWEBOK taxonomy (15 knowledge areas)
├── CLAUDE.md                ← agent instructions (read by Claude Code / Copilot)
├── requirements.txt
└── README.md
```

---

## Notes

* `papers/` and `out/` are gitignored.  Store the spreadsheet and any PDFs
  externally and copy them into `papers/` before running.
* In agent mode there is no extra token cost beyond your existing Claude Code
  or Copilot subscription.
