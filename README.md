# qse-review

Automated pipeline to classify scientific articles on **Quantum Software Engineering (QSE)**
according to the SE knowledge areas defined in [SWEBOK 4th Edition](https://www.computer.org/education/bodies-of-knowledge/software-engineering).
The pipeline produces a frequency histogram showing which SE topics are most
covered in the QSE literature.

The pipeline is designed to be driven by an **AI agent** (Claude Code, GitHub
Copilot, etc.) that reads `CLAUDE.md` for instructions and performs the
classification step itself — no API key required.  An OpenAI API fallback is
also available for fully automated runs.

---

## Pipeline overview

```
papers/*.pdf
    │
    ▼ Step 1 – extract_text.py      (deterministic, no LLM)
out/extracted/*.json
    │
    ▼ Step 2 – agent reads & classifies   ← YOU (or --mode api)
out/classifications/*.json
    │
    ▼ Step 3 – visualize.py         (deterministic, no LLM)
out/analysis/
  ├── histogram.png
  ├── cooccurrence.png
  ├── paper_subjects.csv
  ├── subject_frequencies.json
  └── cooccurrence.json
```

Steps 1 and 3 are fully deterministic and require no API calls.
Step 2 is performed by the agent following the instructions in `CLAUDE.md`,
or optionally via the OpenAI API (`--mode api`).
Already-processed papers are skipped automatically (idempotent).

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

## Installation

```bash
# 1. (Recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Optional: OCR fallback for image-only PDFs
#    pip install pdf2image pytesseract
#    brew install tesseract poppler   # macOS
```

---

## Usage

### Step 1 – Extract text from PDFs

Place your PDF papers in `papers/`, then run:

```bash
python scripts/extract_text.py [--papers-dir papers/] [--output-dir out/extracted/] [--overwrite] [--ocr]
```

Reads every `*.pdf` in `papers/`, extracts text (up to 10 pages per file), and
attempts to isolate the **abstract** section.  Outputs one JSON file per paper
to `out/extracted/`.

Pass `--ocr` (or install `tesseract` to auto-enable it) to activate OCR fallback
for scanned / image-only PDFs.

### Step 2 – Classify SE subjects

#### Option A — Agent mode (default, recommended)

If you are running inside an agentic CLI (Claude Code, GitHub Copilot, etc.),
the agent reads `CLAUDE.md` and classifies each paper directly.  You can check
what work remains at any time:

```bash
python scripts/classify.py          # prints status report of pending papers
python scripts/classify.py --overwrite  # force re-classification of all papers
```

The agent then reads each `out/extracted/<stem>.json`, decides the
classification, and writes `out/classifications/<stem>.json`.

#### Option B — API mode

To classify via the OpenAI API instead of the agent:

```bash
export OPENAI_API_KEY="sk-..."
python scripts/classify.py --mode api [--model gpt-4o-mini] [--delay 0.5] [--overwrite]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--mode` | `agent` | `agent` (status report) or `api` (OpenAI) |
| `--model` | `gpt-4o-mini` | OpenAI model (use `gpt-4o` for higher accuracy) |
| `--delay` | `0.5` | Seconds between API calls (rate-limit buffer) |
| `--overwrite` | off | Re-classify already-classified papers |

### Step 3 – Generate visualisations

```bash
python scripts/visualize.py [--classifications-dir out/classifications/] \
    [--output-dir out/analysis/] [--title "SE Knowledge Areas in QSE Literature"] \
    [--hide-empty] [--min-confidence high|medium|low]
```

Reads all classification files, counts subject frequencies, and produces:

| Output file | Description |
|-------------|-------------|
| `histogram.png` | Horizontal bar chart with all 15 SWEBOK areas |
| `cooccurrence.png` | Heatmap of subject co-occurrence |
| `paper_subjects.csv` | Per-paper table (filename, subjects, summary, …) |
| `subject_frequencies.json` | Subject → paper count mapping |
| `cooccurrence.json` | Raw co-occurrence matrix |

---

## Classification JSON format

Each file written to `out/classifications/<stem>.json` has the following structure:

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

`confidence` is `"high"` when the paper clearly focuses on the area,
`"medium"` for partial overlap, and `"low"` when the mapping is uncertain.

---

## Repository structure

```
qse-review/
├── papers/                  ← place your PDFs here (gitignored)
├── out/                     ← all pipeline output (gitignored; auto-created)
│   ├── extracted/           ← Step 1 output
│   ├── classifications/     ← Step 2 output
│   └── analysis/            ← Step 3 output
├── tests/
│   ├── fixtures/
│   │   └── papers/          ← sample PDFs for testing
│   └── test_pipeline.py
├── scripts/
│   ├── extract_text.py      ← Step 1 script
│   ├── classify.py          ← Step 2 status / API helper
│   └── visualize.py         ← Step 3 script
├── swebok_subjects.json     ← canonical SWEBOK taxonomy (15 knowledge areas)
├── CLAUDE.md                ← agent instructions (classification task)
├── requirements.txt
└── README.md
```

---

## Notes

* `papers/` and `out/` are listed in `.gitignore`; store large PDF corpora
  externally (e.g. Google Drive, DVC, a shared network folder).
* Token cost estimate (API mode): ~500–700 tokens/paper with `gpt-4o-mini`.
  Processing 500 papers costs roughly $0.20–$0.35 USD (as of 2025).
* In agent mode there is no token cost beyond your existing Copilot/Claude
  subscription.
