"""Tests for PDF report generation helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from generate_report import (  # noqa: E402
    PaperRecord,
    build_cooccurrence,
    build_subject_index,
    load_corpus,
    subject_discussion,
)


def test_load_corpus_combines_classification_and_extracted_data(tmp_path):
    classifications_dir = tmp_path / "classifications"
    extracted_dir = tmp_path / "extracted"
    classifications_dir.mkdir()
    extracted_dir.mkdir()

    (classifications_dir / "paper.json").write_text(
        json.dumps(
            {
                "filename": "paper.pdf",
                "stem": "paper",
                "classification": {
                    "subjects": ["Software Testing", "Software Quality"],
                    "primary_subject": "Software Testing",
                    "summary": "Introduces a testing tool for quantum programs.",
                    "confidence": "high",
                },
            }
        ),
        encoding="utf-8",
    )
    (extracted_dir / "paper.json").write_text(
        json.dumps(
            {
                "filename": "paper.pdf",
                "stem": "paper",
                "text_for_classification": "Testing support for quantum software.",
                "full_text": "Testing support for quantum software with benchmarks and tooling.",
                "bibliographic": {
                    "title": "A Testing Tool for Quantum Programs",
                    "year": 2025,
                    "authors": ["Ada Lovelace", "Alan Turing"],
                },
            }
        ),
        encoding="utf-8",
    )

    records = load_corpus(classifications_dir, extracted_dir)

    assert len(records) == 1
    record = records[0]
    assert record.title == "A Testing Tool for Quantum Programs"
    assert record.year == 2025
    assert record.authors == ["Ada Lovelace", "Alan Turing"]
    assert record.primary_subject == "Software Testing"
    assert record.summary == "Introduces a testing tool for quantum programs."


def test_build_subject_index_orders_primary_subject_first():
    records = [
        PaperRecord(
            stem="secondary",
            filename="secondary.pdf",
            title="Secondary",
            year=2024,
            authors=[],
            subjects=["Software Testing"],
            primary_subject="Software Quality",
            summary="Secondary testing subject.",
            confidence="medium",
            text_for_classification="",
            full_text="",
        ),
        PaperRecord(
            stem="primary",
            filename="primary.pdf",
            title="Primary",
            year=2023,
            authors=[],
            subjects=["Software Testing"],
            primary_subject="Software Testing",
            summary="Primary testing subject.",
            confidence="high",
            text_for_classification="",
            full_text="",
        ),
    ]

    index = build_subject_index(records, ["Software Testing"])

    assert [record.stem for record in index["Software Testing"]] == ["primary", "secondary"]


def test_subject_discussion_mentions_count_and_related_subjects():
    records = [
        PaperRecord(
            stem="p1",
            filename="p1.pdf",
            title="Quantum Testing Tool",
            year=2025,
            authors=["Ada"],
            subjects=["Software Testing", "Software Quality"],
            primary_subject="Software Testing",
            summary="Empirical study introducing a testing framework and benchmark for quantum programs.",
            confidence="high",
            text_for_classification="",
            full_text="",
        ),
        PaperRecord(
            stem="p2",
            filename="p2.pdf",
            title="Quantum Testing Benchmark",
            year=2024,
            authors=["Alan"],
            subjects=["Software Testing", "Software Engineering Models and Methods"],
            primary_subject="Software Testing",
            summary="Benchmark and model-driven method for quantum software testing.",
            confidence="high",
            text_for_classification="",
            full_text="",
        ),
    ]

    cooccurrence = build_cooccurrence(
        records,
        ["Software Testing", "Software Quality", "Software Engineering Models and Methods"],
    )
    paragraphs = subject_discussion("Software Testing", records, total_papers=10, cooccurrence=cooccurrence)

    assert any("2" in paragraph for paragraph in paragraphs)
    assert any("Software Quality" in paragraph for paragraph in paragraphs)
    assert any("Software Engineering Models and Methods" in paragraph for paragraph in paragraphs)
