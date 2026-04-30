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

### Step 0 – Resolve DOIs from the spreadsheet

```bash
python scripts/resolve_dois.py [--mailto your@email.com]
```

Reads `papers/QSE - Papers.xlsx`, extracts all papers (year, authors, title,
hyperlink), and resolves a DOI for every entry via:

1. Direct extraction from the cell hyperlink URL (doi.org, dl.acm.org/doi/…)
2. DBLP BibTeX endpoint (for links to dblp.org/rec/…)
3. CrossRef title-search API (general fallback)

Writes `out/dois.json` (the canonical list for the next steps) and
`out/unresolved_papers.json` (papers without a DOI — for manual follow-up).

The script is **incremental**: re-running it only resolves papers that are new
or still lack a DOI; already-resolved papers are reused from the existing
`out/dois.json`.  Use `--overwrite` to reprocess everything from scratch.

### Step 1 – Extract text from PDFs (when PDFs are available)

```bash
python scripts/extract_text.py
```

Reads every PDF in `papers/` and writes one JSON file per paper to
`out/extracted/`.  Pass `--overwrite` to re-extract already-processed papers.

### Step 1b – Fetch metadata from APIs (when PDFs are not available)

```bash
python scripts/fetch_metadata.py [--mailto your@email.com]
```

Reads `out/dois.json`, queries the **Semantic Scholar** API (with CrossRef as
fallback) for each paper and writes one JSON file per paper to `out/extracted/`
using the same schema as `extract_text.py`.  Use this step instead of Step 1
when the PDFs are not locally available.  Pass `--overwrite` to re-fetch
already-processed papers.

### Step 2 – Classify each paper (your task)

For every file `out/extracted/<stem>.json` that does **not** already have a
corresponding `out/classifications/<stem>.json`:

1. Open `out/extracted/<stem>.json` and read the `text_for_classification`
   field (abstract or first ~1 500 words).
2. Classify the paper — decide which SWEBOK knowledge areas it addresses.
3. Write the result to `out/classifications/<stem>.json` using the exact
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

Reads `out/classifications/*.json` and writes to `out/analysis/`:

- `histogram.png` – bar chart of subject frequencies (all 15 SWEBOK areas)
- `cooccurrence.png` – heatmap of subject co-occurrence
- `paper_subjects.csv` – per-paper table
- `subject_frequencies.json` – aggregate counts
- `cooccurrence.json` – raw co-occurrence matrix

---

## Required JSON format for each classification

Write raw JSON (no markdown fences) to `out/classifications/<stem>.json`:

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

The canonical list lives in `swebok_subjects.json` at the project root.
Current values:

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

- **Incrementality**: every script in the pipeline is incremental by design.
  Re-running any step only processes entries that are new or not yet handled;
  already-produced output files are preserved and skipped.  This means that if
  new papers are added to the spreadsheet you can simply re-run the pipeline
  from Step 0 and only the new entries will be processed end-to-end.
  Pass `--overwrite` to any script to force reprocessing of all entries.
- **Partial runs**: if you stop mid-way, already-written files are preserved —
  just resume from where you left off.
- A paper may be tagged with **multiple** subjects; only include areas that are
  clearly relevant.
