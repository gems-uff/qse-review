# qse-review

Automated pipeline to classify scientific articles on **Quantum Software Engineering (QSE)**
according to the SE knowledge areas defined in [SWEBOK 4th Edition](https://www.computer.org/education/bodies-of-knowledge/software-engineering).
The pipeline produces a frequency histogram showing which SE topics are most
covered in the QSE literature.

---

## Pipeline overview

```
papers/*.pdf
    │
    ▼ Step 1 – extract_text.py  (deterministic, no LLM)
data/extracted/*.json
    │
    ▼ Step 2 – classify.py      (LLM, uses OpenAI API)
data/classifications/*.json
    │
    ▼ Step 3 – visualize.py     (deterministic, no LLM)
data/output/
  ├── histogram.png
  ├── paper_subjects.csv
  └── subject_frequencies.json
```

Steps 1 and 3 are fully deterministic and require no API calls.  
Step 2 sends only a short excerpt per paper (abstract or first ~1 500 words)
to minimise token consumption.  Already-processed papers are skipped
automatically (idempotent).

---

## SE Knowledge Areas (taxonomy)

The taxonomy follows **SWEBOK 4th Edition**:

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
```

---

## Usage

### Quick start – run the full pipeline

```bash
export OPENAI_API_KEY="sk-..."   # required for Step 2 only

# Place your PDF papers in the papers/ directory, then:
bash run_pipeline.sh
```

Pass `--overwrite` to re-process papers that were already extracted / classified:

```bash
bash run_pipeline.sh --overwrite
```

---

### Step-by-step

#### Step 1 – Extract text from PDFs

```bash
python scripts/extract_text.py [--papers-dir papers/] [--output-dir data/extracted/] [--overwrite]
```

Reads every `*.pdf` in `papers/`, extracts text (up to 10 pages per file), and
attempts to isolate the **abstract** section.  Outputs one JSON file per paper
to `data/extracted/`.

#### Step 2 – Classify SE subjects

```bash
export OPENAI_API_KEY="sk-..."
python scripts/classify.py [--extracted-dir data/extracted/] [--output-dir data/classifications/] \
    [--model gpt-4o-mini] [--delay 0.5] [--overwrite]
```

Sends the short `text_for_classification` excerpt from each extracted file to
the OpenAI chat API and returns a structured JSON with the identified SWEBOK
knowledge areas.  Outputs one JSON file per paper to `data/classifications/`.

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `gpt-4o-mini` | OpenAI model (use `gpt-4o` for higher accuracy) |
| `--delay` | `0.5` | Seconds between API calls (rate-limit buffer) |
| `--overwrite` | off | Re-classify already-classified papers |

#### Step 3 – Generate histogram

```bash
python scripts/visualize.py [--classifications-dir data/classifications/] \
    [--output-dir data/output/] [--title "SE Knowledge Areas in QSE Literature"]
```

Reads all classification files, counts subject frequencies, and produces:

| Output file | Description |
|-------------|-------------|
| `histogram.png` | Horizontal bar chart sorted by frequency |
| `paper_subjects.csv` | Per-paper table (filename, subjects, summary, …) |
| `subject_frequencies.json` | Subject → paper count mapping |

---

## Repository structure

```
qse-review/
├── papers/                  ← upload your PDFs here (not tracked by git)
├── data/
│   ├── extracted/           ← Step 1 output  (not tracked by git)
│   ├── classifications/     ← Step 2 output  (not tracked by git)
│   └── output/              ← Step 3 output  (tracked by git)
├── scripts/
│   ├── extract_text.py      ← Step 1 script
│   ├── classify.py          ← Step 2 script
│   └── visualize.py         ← Step 3 script
├── run_pipeline.sh          ← runs all three steps in sequence
├── requirements.txt
└── README.md
```

---

## Notes

* `papers/` and `data/extracted/` are listed in `.gitignore`; store large
  PDF corpora externally (e.g. Google Drive, DVC, a shared network folder).
* `data/classifications/` is also gitignored; commit only the final
  `data/output/` artefacts.
* Token cost estimate: ~500–700 tokens/paper with `gpt-4o-mini`.
  Processing 500 papers costs roughly $0.20–$0.35 USD (as of 2025).
