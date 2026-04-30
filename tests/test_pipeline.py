"""Tests for the qse-review pipeline — covering classification and aggregation."""

import json
import sys
from pathlib import Path

import pytest

# Allow imports from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from classify import _validate_classification, SE_SUBJECTS, SE_SUBJECTS_SET  # noqa: E402
from visualize import load_classifications  # noqa: E402


# ---------------------------------------------------------------------------
# SWEBOK taxonomy
# ---------------------------------------------------------------------------

def test_swebok_subjects_count():
    assert len(SE_SUBJECTS) == 15, f"Expected 15 subjects, got {len(SE_SUBJECTS)}"


def test_swebok_subjects_file_exists():
    subjects_path = Path(__file__).parent.parent / "swebok_subjects.json"
    assert subjects_path.exists(), "swebok_subjects.json not found at project root"
    with open(subjects_path) as f:
        data = json.load(f)
    assert len(data) == 15


# ---------------------------------------------------------------------------
# Classification validation
# ---------------------------------------------------------------------------

VALID_CLF = {
    "subjects": ["Software Testing", "Software Quality"],
    "primary_subject": "Software Testing",
    "summary": "A one-sentence summary.",
    "confidence": "high",
}


def test_validate_valid_classification():
    errors = _validate_classification(VALID_CLF, "test_paper")
    assert errors == []


def test_validate_unknown_subject():
    clf = {**VALID_CLF, "subjects": ["Software Testing", "Unknown Area"]}
    errors = _validate_classification(clf, "test_paper")
    assert any("unknown subjects" in e for e in errors)


def test_validate_primary_not_in_subjects():
    clf = {**VALID_CLF, "primary_subject": "Software Architecture"}
    errors = _validate_classification(clf, "test_paper")
    assert any("primary_subject" in e for e in errors)


def test_validate_invalid_confidence():
    clf = {**VALID_CLF, "confidence": "certain"}
    errors = _validate_classification(clf, "test_paper")
    assert any("confidence" in e for e in errors)


def test_validate_empty_subjects():
    clf = {**VALID_CLF, "subjects": []}
    errors = _validate_classification(clf, "test_paper")
    assert errors


# ---------------------------------------------------------------------------
# load_classifications
# ---------------------------------------------------------------------------

def test_load_classifications_skips_invalid(tmp_path):
    valid = {
        "filename": "good.pdf",
        "stem": "good",
        "classification": {
            "subjects": ["Software Testing"],
            "primary_subject": "Software Testing",
            "summary": "A study.",
            "confidence": "high",
        },
    }
    invalid_subject = {
        "filename": "bad.pdf",
        "stem": "bad",
        "classification": {
            "subjects": ["Not A Real Area"],
            "primary_subject": "Not A Real Area",
            "summary": "A study.",
            "confidence": "high",
        },
    }
    error_file = {
        "filename": "err.pdf",
        "stem": "err",
        "classification": {"error": "extraction failed"},
    }
    (tmp_path / "good.json").write_text(json.dumps(valid))
    (tmp_path / "bad.json").write_text(json.dumps(invalid_subject))
    (tmp_path / "err.json").write_text(json.dumps(error_file))

    records = load_classifications(tmp_path)
    assert len(records) == 1
    assert records[0]["stem"] == "good"


def test_load_classifications_aggregation(tmp_path):
    papers = [
        {
            "filename": f"p{i}.pdf",
            "stem": f"p{i}",
            "classification": {
                "subjects": ["Software Testing", "Software Quality"],
                "primary_subject": "Software Testing",
                "summary": "Summary.",
                "confidence": "high",
            },
        }
        for i in range(3)
    ]
    for p in papers:
        (tmp_path / f"{p['stem']}.json").write_text(json.dumps(p))

    records = load_classifications(tmp_path)
    assert len(records) == 3
    from collections import Counter
    counter: Counter = Counter()
    for r in records:
        for s in r["classification"]["subjects"]:
            counter[s] += 1
    assert counter["Software Testing"] == 3
    assert counter["Software Quality"] == 3
