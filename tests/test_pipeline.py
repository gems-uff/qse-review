"""Tests for the qse-review pipeline — covering extraction and aggregation."""

import json
import sys
from pathlib import Path

import pytest

# Allow imports from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from extract_text import _extract_abstract, MIN_ABSTRACT_LENGTH  # noqa: E402
from classify import _validate_classification, SE_SUBJECTS, SE_SUBJECTS_SET  # noqa: E402
from visualize import load_classifications  # noqa: E402


# ---------------------------------------------------------------------------
# Abstract extraction
# ---------------------------------------------------------------------------

IEEE_TEXT = (
    "Title of Paper\n"
    "Abstract— Quantum software engineering (QSE) is an emerging field that "
    "applies SE principles to quantum programs. We survey existing approaches "
    "and identify key challenges in testing and verification of quantum circuits.\n"
    "Keywords— quantum, software engineering, testing\n"
    "I. Introduction\nThis paper..."
)

ACM_TEXT = (
    "Title of Paper\n\n"
    "Abstract\n\n"
    "Quantum software engineering (QSE) is an emerging field that applies SE "
    "principles to quantum programs. We survey existing approaches and identify "
    "key challenges in testing and verification of quantum circuits, aiming to "
    "bridge classical SE and quantum computing.\n\n"
    "1. Introduction\nThis paper..."
)

SPRINGER_TEXT = (
    "Title of Paper\n\n"
    "Abstract. Quantum software engineering (QSE) is an emerging field that "
    "applies SE principles to quantum programs. We survey approaches and "
    "identify key challenges in testing and verification of quantum circuits.\n\n"
    "1 Introduction\nThis paper..."
)

COLON_TEXT = (
    "Abstract: Quantum software engineering applies SE principles to quantum "
    "programs and covers testing, verification, and quality assurance for "
    "quantum circuits in hybrid classical-quantum architectures.\n\n"
    "1. Introduction"
)

NO_ABSTRACT_TEXT = (
    "1. Introduction\nThis paper is about quantum computing.\n"
    "2. Related Work\nSeveral works exist.\n"
)


def test_extract_abstract_ieee():
    result = _extract_abstract(IEEE_TEXT)
    assert result is not None
    assert "Quantum software engineering" in result
    assert len(result) > MIN_ABSTRACT_LENGTH


def test_extract_abstract_acm():
    result = _extract_abstract(ACM_TEXT)
    assert result is not None
    assert "Quantum software engineering" in result


def test_extract_abstract_springer():
    result = _extract_abstract(SPRINGER_TEXT)
    assert result is not None
    assert "Quantum software engineering" in result


def test_extract_abstract_colon():
    result = _extract_abstract(COLON_TEXT)
    assert result is not None
    assert "quantum programs" in result


def test_extract_abstract_missing():
    result = _extract_abstract(NO_ABSTRACT_TEXT)
    assert result is None


# ---------------------------------------------------------------------------
# SWEBOK taxonomy
# ---------------------------------------------------------------------------

def test_swebok_subjects_count():
    assert len(SE_SUBJECTS) == 15, f"Expected 15 subjects, got {len(SE_SUBJECTS)}"


def test_swebok_subjects_file_exists():
    subjects_path = Path(__file__).parent.parent / "data" / "swebok_subjects.json"
    assert subjects_path.exists(), "data/swebok_subjects.json not found"
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
