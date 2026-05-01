# QSE Review – Agent Instructions

You are helping classify scientific papers on **Quantum Software Engineering (QSE)**
according to the SWEBOK (Software Engineering Body of Knowledge) 4th Edition
knowledge areas.

---

## Your role as orchestrator

You drive the entire pipeline.  No shell script is needed — just follow the
steps below in order.

When the user says **"Rode o pipeline"** (or an equivalent request), run the
**full pipeline in order, one program at a time**. Show the output of each step
before moving to the next one, so the user can inspect the result and decide
whether to continue.

---

## Pipeline

### Step 0 – Resolve DOIs from the spreadsheets

```bash
python scripts/resolve_dois.py [--mailto your@email.com]
```

Scans `papers/` for all `.xlsx` files, extracts all papers (year, authors, title,
hyperlink), and resolves a DOI for every entry via:

1. Direct extraction from the cell hyperlink URL (doi.org, dl.acm.org/doi/…)
2. DBLP BibTeX endpoint (for links to dblp.org/rec/…)
3. CrossRef title-search API (general fallback)

Writes `out/dois.json` (the canonical list for the next steps) and
`out/unresolved_papers.json` (papers without a DOI — for manual follow-up).

The script is **incremental**: re-running it only resolves papers that are new
or still lack a DOI; already-resolved papers are reused from the existing
`out/dois.json`.  Use `--overwrite` to reprocess everything from scratch.
Use `--input <path>` to pass a specific file or directory instead of the default `papers/`.

### Step 1 – Fetch metadata from APIs

```bash
python scripts/fetch_metadata.py [--mailto your@email.com]
```

Reads `out/dois.json`, queries the **Semantic Scholar** API (with CrossRef as
fallback) for each paper and writes one JSON file per paper to `out/extracted/`.
Pass `--overwrite` to re-fetch already-processed papers, `--delay N` to adjust
the seconds between API calls (default `1.0`), or `--limit N` to stop after
processing `N` papers.

### Step 1b – Enrich extracted JSONs from local PDFs

```bash
python scripts/enrich_from_pdfs.py [--update-dois] [--ocr] [--crossref]
```

When `papers/` contains the actual PDFs, run this after `fetch_metadata.py` to
recover fuller text, classification context, and possible DOI hints directly
from the PDFs. The script matches each PDF against the existing
`out/extracted/*.json` files, updates them in place, and writes
`out/pdf_enrichment_report.json` summarizing enriched, unmatched, and ambiguous
cases. If the metadata APIs did not provide an abstract, the enrichment step
uses the first 1200 characters of the cleaned PDF text as
`text_for_classification`. With `--update-dois`, it also propagates uniquely
recovered DOIs back to `out/dois.json` and regenerates
`out/unresolved_papers.json`. The step is incremental: unchanged PDFs are
skipped based on `out/pdf_enrichment_state.json` unless `--overwrite` is used,
while previously unmatched PDFs are retried when the extracted catalog changes.
By default this enrichment step is local-only and does not re-query DOI
metadata services; use `--crossref` only when that extra bibliographic
reconciliation is desired.

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

That is the default `--mode agent` behaviour: it lists every unclassified
paper and a short excerpt so you know what work remains.

To classify all papers via the OpenAI API instead of doing it yourself:

```bash
export OPENAI_API_KEY="sk-..."
python scripts/classify.py --mode api [--model gpt-4o-mini] [--limit N] [--overwrite]
```

`--model` selects the OpenAI model (default `gpt-4o-mini`), `--limit N` stops
after `N` papers, and `--overwrite` re-classifies entries that already have a
file in `out/classifications/`.

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

Useful flags: `--hide-empty` (drop subjects with zero papers from the
histogram), `--min-confidence {low,medium,high}` (ignore classifications below
this confidence), and `--title TEXT` (custom histogram title).

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
