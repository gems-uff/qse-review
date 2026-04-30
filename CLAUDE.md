# QSE Review – Agent Instructions

You are helping classify scientific papers on **Quantum Software Engineering (QSE)**
according to the SWEBOK (Software Engineering Body of Knowledge) 4th Edition
knowledge areas.

---

## Your role as orchestrator

You drive the entire pipeline.  No shell script is needed — just follow the
steps below in order.

---

## Pipeline

### Step 1 – Extract text from PDFs

```bash
python scripts/extract_text.py
```

Reads every PDF in `papers/` and writes one JSON file per paper to
`data/extracted/`.  Pass `--overwrite` to re-extract already-processed papers.

### Step 2 – Classify each paper (your task)

For every file `data/extracted/<stem>.json` that does **not** already have a
corresponding `data/classifications/<stem>.json`:

1. Open `data/extracted/<stem>.json` and read the `text_for_classification`
   field (abstract or first ~1 500 words).
2. Classify the paper — decide which SWEBOK knowledge areas it addresses.
3. Write the result to `data/classifications/<stem>.json` using the exact
   format shown below.

Optionally run the following command first to see which papers still need
classification:

```bash
python scripts/classify.py
```

That command lists every unclassified paper and a short excerpt so you know
what work remains.

To classify all papers via the OpenAI API instead of doing it yourself:

```bash
export OPENAI_API_KEY="sk-..."
python scripts/classify.py --mode api
```

### Step 3 – Generate visualisations

```bash
python scripts/visualize.py
```

Reads `data/classifications/*.json` and writes to `data/output/`:

- `histogram.png` – bar chart of subject frequencies
- `paper_subjects.csv` – per-paper table
- `subject_frequencies.json` – aggregate counts

---

## Required JSON format for each classification

Write raw JSON (no markdown fences) to `data/classifications/<stem>.json`:

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

### Valid `subjects` values — use verbatim (exact match required)

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

### Confidence levels

| Value | Meaning |
|---|---|
| `high` | Paper clearly focuses on the knowledge area |
| `medium` | Paper partially overlaps with the area |
| `low` | Mapping is uncertain or peripheral |

---

## Tips

- **Idempotency**: all scripts skip already-processed files by default.
  Pass `--overwrite` to redo a step.
- **Partial runs**: if you stop mid-way, already-written classification files
  are preserved — just resume from where you left off.
- A paper may be tagged with **multiple** subjects; only include areas that are
  clearly relevant.
