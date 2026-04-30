"""Tests for the qse-review pipeline."""

import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Allow imports from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from enrich_from_pdfs import _doi_from_filename, _match_record, _merge_record, main as enrich_main  # noqa: E402
from fetch_metadata import main as fetch_metadata_main  # noqa: E402
from classify import _validate_classification, SE_SUBJECTS, SE_SUBJECTS_SET  # noqa: E402
from extract_text import (  # noqa: E402
    MIN_ABSTRACT_LENGTH,
    _clean_extracted_text,
    _extract_abstract,
    _extract_bibliographic,
    _extract_doi,
    _fetch_crossref,
)
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

NO_ABSTRACT_TEXT = (
    "1. Introduction\nThis paper is about quantum computing.\n"
    "2. Related Work\nSeveral works exist.\n"
)

NOISY_PDF_TEXT = (
    "Version of Record: https://www.sciencedirect.com/science/article/pii/S0164121223002005\n"
    "Manuscript_93e37fab054653f60d2a7b792c7be0ad\n"
    "Bugs4Q: A Benchmark of Existing Bugs to Enable Controlled Testing\n"
    "and Debugging Studies for Quantum Programs\n"
    "PengzhanZhao, ZhongtaoMiao and JianjunZhao\n"
    "ARTICLE INFO\n"
    "ABSTRACT\n"
    "Realistic benchmarks of reproducible bugs and fixes are vital to good experimental "
    "evaluation of debugging and testing approaches for quantum programs.\n"
    "Keywords: Quantumsoftwaretesting\n"
    "1 INTRODUCTION\n"
)


def test_extract_abstract_ieee():
    result = _extract_abstract(IEEE_TEXT)
    assert result is not None
    assert "Quantum software engineering" in result
    assert len(result) > MIN_ABSTRACT_LENGTH


def test_extract_abstract_acm():
    result = _extract_abstract(ACM_TEXT)
    assert result is not None
    assert "bridge classical SE" in result


def test_extract_abstract_springer():
    result = _extract_abstract(SPRINGER_TEXT)
    assert result is not None
    assert "identify key challenges" in result


def test_extract_abstract_missing():
    assert _extract_abstract(NO_ABSTRACT_TEXT) is None


def test_clean_extracted_text_removes_editorial_noise():
    cleaned = _clean_extracted_text(NOISY_PDF_TEXT)
    assert "Version of Record" not in cleaned
    assert "Manuscript_" not in cleaned
    assert "ARTICLE INFO" not in cleaned
    assert "Pengzhan Zhao, Zhongtao Miao and Jianjun Zhao" in cleaned


def test_extract_abstract_after_cleaning_noisy_pdf_text():
    cleaned = _clean_extracted_text(NOISY_PDF_TEXT)
    result = _extract_abstract(cleaned)
    assert result is not None
    assert "Realistic benchmarks of reproducible bugs and fixes" in result


# ---------------------------------------------------------------------------
# DOI extraction and CrossRef parsing
# ---------------------------------------------------------------------------


def test_extract_doi_plain():
    assert _extract_doi("DOI: 10.1145/3597503.3597515") == "10.1145/3597503.3597515"


def test_extract_doi_url():
    assert _extract_doi("https://doi.org/10.1109/TSE.2023.001") == "10.1109/TSE.2023.001"


def test_extract_doi_missing():
    assert _extract_doi("No DOI in this text at all.") is None


_CROSSREF_RESPONSE = {
    "status": "ok",
    "message": {
        "title": ["Automated Testing of Quantum Circuits"],
        "author": [
            {"given": "Alice", "family": "Smith"},
            {"given": "Bob", "family": "Jones"},
        ],
        "published": {"date-parts": [[2023, 6, 1]]},
        "container-title": ["IEEE Transactions on Software Engineering"],
        "type": "journal-article",
    },
}


def _mock_urlopen(req, timeout=None):
    import io
    import urllib.response

    body = json.dumps(_CROSSREF_RESPONSE).encode()
    return urllib.response.addinfourl(io.BytesIO(body), {}, req.full_url, 200)


def test_fetch_crossref_parses_response():
    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        result = _fetch_crossref("10.1109/TSE.2023.001")
    assert result["title"] == "Automated Testing of Quantum Circuits"
    assert result["year"] == 2023
    assert result["authors"] == ["Alice Smith", "Bob Jones"]
    assert result["venue"] == "IEEE Transactions on Software Engineering"
    assert result["venue_type"] == "journal-article"


# ---------------------------------------------------------------------------
# Bibliographic extraction
# ---------------------------------------------------------------------------

BIB_WITH_DOI = (
    "A Paper With a DOI\n"
    "DOI: 10.1109/TSE.2023.001\n"
    "Abstract: Content here.\n"
)


def test_bibliographic_uses_crossref_when_doi_found():
    with patch(
        "extract_text._fetch_crossref",
        return_value={
            "title": "CrossRef Title",
            "year": 2023,
            "authors": ["Alice Smith"],
            "venue": "IEEE TSE",
            "venue_type": "journal-article",
        },
    ):
        bio = _extract_bibliographic(BIB_WITH_DOI)
    assert bio["source"] == "crossref"
    assert bio["title"] == "CrossRef Title"
    assert bio["doi"] == "10.1109/TSE.2023.001"


def test_bibliographic_skips_crossref_when_disabled():
    with patch("extract_text._fetch_crossref") as fetch_crossref:
        bio = _extract_bibliographic(BIB_WITH_DOI, use_crossref=False)
    fetch_crossref.assert_not_called()
    assert bio["doi"] == "10.1109/TSE.2023.001"
    assert bio["source"] == "heuristic"


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


# ---------------------------------------------------------------------------
# PDF enrichment helpers
# ---------------------------------------------------------------------------


def test_match_record_prefers_doi(tmp_path):
    extracted_path = tmp_path / "10_1145_123.json"
    extracted_path.write_text(
        json.dumps(
            {
                "filename": "10_1145_123.pdf",
                "stem": "10_1145_123",
                "abstract": None,
                "text_for_classification": "Short title",
                "bibliographic": {"doi": "10.1145/123", "title": "Example Paper"},
            }
        ),
        encoding="utf-8",
    )
    _, index = __import__("enrich_from_pdfs")._build_index(tmp_path)
    record, match_by, candidates = _match_record(
        Path("any.pdf"),
        {"bibliographic": {"doi": "10.1145/123", "title": "Other"}},
        index,
    )
    assert record is not None
    assert match_by == "doi"
    assert candidates == []


def test_match_record_falls_back_to_noisy_title(tmp_path):
    extracted_path = tmp_path / "10_1016_j_infsof_2023_107249.json"
    extracted_path.write_text(
        json.dumps(
            {
                "filename": "10_1016_j_infsof_2023_107249.pdf",
                "stem": "10_1016_j_infsof_2023_107249",
                "abstract": None,
                "text_for_classification": "Making existing software quantum safe: A case study on IBM Db2",
                "bibliographic": {
                    "doi": "10.1016/j.infsof.2023.107249",
                    "title": "Making existing software quantum safe: A case study on IBM Db2",
                },
            }
        ),
        encoding="utf-8",
    )
    _, index = __import__("enrich_from_pdfs")._build_index(tmp_path)
    record, match_by, candidates = _match_record(
        Path("2110.08661v2.pdf"),
        {
            "bibliographic": {
                "title": (
                    "Making existing software quantum safe: a case study on IBM Db2 "
                    "Lei Zhang1, Andriy Miranskyy1, Walid Rjaibi2"
                )
            }
        },
        index,
    )
    assert record is not None
    assert record["_path"].name == "10_1016_j_infsof_2023_107249.json"
    assert match_by == "title-fuzzy"
    assert candidates == []


def test_match_record_falls_back_to_title_with_prefix_noise(tmp_path):
    extracted_path = tmp_path / "10_1016_j_jss_2023_111805.json"
    extracted_path.write_text(
        json.dumps(
            {
                "filename": "10_1016_j_jss_2023_111805.pdf",
                "stem": "10_1016_j_jss_2023_111805",
                "abstract": None,
                "text_for_classification": "Bugs4Q: A benchmark of existing bugs to enable controlled testing and debugging studies for quantum programs",
                "bibliographic": {
                    "doi": "10.1016/j.jss.2023.111805",
                    "title": "Bugs4Q: A benchmark of existing bugs to enable controlled testing and debugging studies for quantum programs",
                },
            }
        ),
        encoding="utf-8",
    )
    _, index = __import__("enrich_from_pdfs")._build_index(tmp_path)
    record, match_by, candidates = _match_record(
        Path("1-s2.0-S0164121223002005-am.pdf"),
        {
            "bibliographic": {
                "title": (
                    "Manuscript_93e37fab054653f60d2a7b792c7be0ad Bugs4Q: "
                    "A Benchmark of Existing Bugs to Enable Controlled Testing "
                    "and Debugging Studies for Quantum Programs"
                )
            }
        },
        index,
    )
    assert record is not None
    assert record["_path"].name == "10_1016_j_jss_2023_111805.json"
    assert match_by == "title"
    assert candidates == []


def test_doi_from_filename_supports_acm_and_springer():
    assert _doi_from_filename(Path("3764582.pdf")) == "10.1145/3764582"
    assert _doi_from_filename(Path("3412451.3428497.pdf")) == "10.1145/3412451.3428497"
    assert _doi_from_filename(Path("s10664-024-10461-9.pdf")) == "10.1007/s10664-024-10461-9"


def test_merge_record_recovers_abstract_and_bibliographic():
    existing = {
        "filename": "Paper.pdf",
        "stem": "Paper",
        "pages_extracted": 0,
        "full_text": "",
        "abstract": None,
        "text_for_classification": "Industry Expectations and Skill Demands in Quantum Software Testing",
        "bibliographic": {
            "doi": None,
            "title": "Industry Expectations and Skill Demands in Quantum Software Testing",
            "year": 2026,
            "authors": ["Ronnie de Souza Santos"],
            "venue": "Q-SE",
            "source": "spreadsheet",
        },
        "ocr_used": False,
        "error": None,
    }
    pdf_result = {
        "pages_extracted": 3,
        "full_text": "Longer body text",
        "abstract": "This is a recovered abstract with enough detail to exceed the minimum length. "
        "It explains skill demands and industry expectations in quantum software testing.",
        "text_for_classification": "This is a recovered abstract with enough detail to exceed the minimum length. "
        "It explains skill demands and industry expectations in quantum software testing.",
        "bibliographic": {
            "doi": "10.1000/example",
            "title": "Industry Expectations and Skill Demands in Quantum Software Testing",
            "year": 2026,
            "authors": ["Ronnie de Souza Santos", "Maria Teresa Baldassarre"],
            "venue": "Q-SE",
            "venue_type": "proceedings-article",
            "source": "crossref",
        },
        "ocr_used": False,
    }
    merged, info = _merge_record(existing, pdf_result, Path("paper.pdf"), "title")
    assert merged["abstract"] == pdf_result["abstract"]
    assert merged["text_for_classification"] == pdf_result["abstract"]
    assert merged["bibliographic"]["doi"] == "10.1000/example"
    assert "bibliographic.doi" in info["updated_fields"]


def test_enrich_main_updates_extracted_and_doi_catalog(tmp_path, monkeypatch):
    papers_dir = tmp_path / "papers"
    extracted_dir = tmp_path / "extracted"
    papers_dir.mkdir()
    extracted_dir.mkdir()

    (papers_dir / "Industry_Expectations_and_Skill_Demands_in_Quantum_Software_Testing.pdf").write_bytes(b"%PDF-1.4\n")
    (extracted_dir / "Industry_Expectations_and_Skill_Demands_in_Quantum_Software_Testing.json").write_text(
        json.dumps(
            {
                "filename": "Industry_Expectations_and_Skill_Demands_in_Quantum_Software_Testing.pdf",
                "stem": "Industry_Expectations_and_Skill_Demands_in_Quantum_Software_Testing",
                "pages_extracted": 0,
                "full_text": "",
                "abstract": None,
                "text_for_classification": "Industry Expectations and Skill Demands in Quantum Software Testing",
                "bibliographic": {
                    "doi": None,
                    "title": "Industry Expectations and Skill Demands in Quantum Software Testing",
                    "year": 2026,
                    "authors": ["Ronnie de Souza Santos"],
                    "venue": "Q-SE",
                    "source": "spreadsheet",
                },
                "ocr_used": False,
                "error": None,
            }
        ),
        encoding="utf-8",
    )

    dois_path = tmp_path / "dois.json"
    unresolved_path = tmp_path / "unresolved.json"
    report_path = tmp_path / "report.json"
    doi_records = [
        {
            "sheet": "Q-SE",
            "year": 2026,
            "authors": "Ronnie de Souza Santos, Maria Teresa Baldassarre, César França",
            "title": "Industry Expectations and Skill Demands in Quantum Software Testing",
            "doi": None,
            "url": "https://arxiv.org/pdf/2512.14861",
            "doi_source": None,
        }
    ]
    dois_path.write_text(json.dumps(doi_records), encoding="utf-8")
    unresolved_path.write_text(json.dumps(doi_records), encoding="utf-8")

    def fake_extract_text_from_pdf(*args, **kwargs):
        abstract = (
            "This paper studies industry expectations and required skills for quantum software testing, "
            "including practitioner concerns, tooling expectations, and workforce preparation."
        )
        return {
            "filename": args[0].name,
            "stem": args[0].stem,
            "pages_extracted": 2,
            "full_text": abstract + " Full paper body.",
            "abstract": abstract,
            "text_for_classification": abstract,
            "bibliographic": {
                "doi": "10.5555/qsetest.2026.1",
                "title": "Industry Expectations and Skill Demands in Quantum Software Testing",
                "year": 2026,
                "authors": [
                    "Ronnie de Souza Santos",
                    "Maria Teresa Baldassarre",
                    "César França",
                ],
                "venue": "Q-SE",
                "venue_type": "proceedings-article",
                "source": "crossref",
            },
            "ocr_used": False,
            "error": None,
        }

    monkeypatch.setattr("enrich_from_pdfs.extract_text_from_pdf", fake_extract_text_from_pdf)
    enrich_main(
        [
            "--papers-dir",
            str(papers_dir),
            "--extracted-dir",
            str(extracted_dir),
            "--dois-path",
            str(dois_path),
            "--unresolved-path",
            str(unresolved_path),
            "--report-path",
            str(report_path),
            "--update-dois",
        ]
    )

    enriched = json.loads(next(extracted_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert enriched["abstract"] is not None
    assert enriched["bibliographic"]["doi"] == "10.5555/qsetest.2026.1"

    updated_dois = json.loads(dois_path.read_text(encoding="utf-8"))
    assert updated_dois[0]["doi"] == "10.5555/qsetest.2026.1"
    assert json.loads(unresolved_path.read_text(encoding="utf-8")) == []


def test_enrich_main_skips_unchanged_pdf_on_second_run(tmp_path, monkeypatch, caplog):
    papers_dir = tmp_path / "papers"
    extracted_dir = tmp_path / "extracted"
    papers_dir.mkdir()
    extracted_dir.mkdir()

    pdf_path = papers_dir / "3764582.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    target_json = extracted_dir / "10_1145_3764582.json"
    target_json.write_text(
        json.dumps(
            {
                "filename": "10_1145_3764582.pdf",
                "stem": "10_1145_3764582",
                "pages_extracted": 0,
                "full_text": "",
                "abstract": None,
                "text_for_classification": "A Survey of Quantum Machine Learning",
                "bibliographic": {
                    "doi": "10.1145/3764582",
                    "title": "A Survey of Quantum Machine Learning",
                    "year": 2025,
                    "authors": ["A. Researcher"],
                    "venue": "ACM Computing Surveys",
                    "source": "semantic_scholar",
                },
                "ocr_used": False,
                "error": None,
            }
        ),
        encoding="utf-8",
    )

    report_path = tmp_path / "report.json"
    state_path = tmp_path / "state.json"
    calls = {"count": 0}

    def fake_extract_text_from_pdf(*args, **kwargs):
        calls["count"] += 1
        abstract = (
            "This survey reviews quantum machine learning foundations, algorithms, frameworks, "
            "datasets, and applications, with enough detail to count as a real abstract."
        )
        return {
            "filename": args[0].name,
            "stem": args[0].stem,
            "pages_extracted": 2,
            "full_text": abstract + " Full body.",
            "abstract": abstract,
            "text_for_classification": abstract,
            "bibliographic": {
                "doi": "10.1145/3764582",
                "title": "A Survey of Quantum Machine Learning",
                "year": 2025,
                "authors": ["A. Researcher"],
                "venue": "ACM Computing Surveys",
                "venue_type": "journal-article",
                "source": "crossref",
            },
            "ocr_used": False,
            "error": None,
        }

    monkeypatch.setattr("enrich_from_pdfs.extract_text_from_pdf", fake_extract_text_from_pdf)

    args = [
        "--papers-dir",
        str(papers_dir),
        "--extracted-dir",
        str(extracted_dir),
        "--report-path",
        str(report_path),
        "--state-path",
        str(state_path),
    ]
    enrich_main(args)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        enrich_main(args)

    assert calls["count"] == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["skipped"] == [{"pdf": "3764582.pdf", "reason": "already enriched"}]
    messages = [record.getMessage() for record in caplog.records]
    assert "PDF enrichment queue — to process: 0  skipped: 1" in messages
    assert not any("skip (already enriched)" in message for message in messages)


def test_fetch_metadata_summarizes_skips_without_per_item_logs(tmp_path, monkeypatch, caplog):
    dois_path = tmp_path / "dois.json"
    extracted_dir = tmp_path / "extracted"
    extracted_dir.mkdir()

    papers = [
        {
            "sheet": "Q-SE",
            "year": 2025,
            "authors": "A. Researcher",
            "title": "Already Fetched Paper",
            "doi": "10.1000/already-fetched",
            "url": None,
            "doi_source": "spreadsheet",
        },
        {
            "sheet": "Q-SE",
            "year": 2025,
            "authors": "B. Researcher",
            "title": "Pending Paper",
            "doi": "10.1000/pending-paper",
            "url": None,
            "doi_source": "spreadsheet",
        },
    ]
    dois_path.write_text(json.dumps(papers), encoding="utf-8")
    (extracted_dir / "10_1000_already-fetched.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr("fetch_metadata.DOIS_PATH", dois_path)
    monkeypatch.setattr("fetch_metadata.EXTRACTED_DIR", extracted_dir)
    monkeypatch.setattr(
        "fetch_metadata._s2_by_doi",
        lambda doi, mailto=None: {
            "title": "Pending Paper",
            "year": 2025,
            "authors": [{"name": "B. Researcher"}],
            "abstract": "A sufficiently detailed abstract for the pending paper.",
            "venue": "Q-SE",
        },
    )
    monkeypatch.setattr("fetch_metadata._s2_by_title", lambda title, mailto=None: None)
    monkeypatch.setattr("fetch_metadata._crossref_by_doi", lambda doi, mailto=None: None)
    monkeypatch.setattr("fetch_metadata.time.sleep", lambda seconds: None)

    with patch.object(sys, "argv", ["fetch_metadata.py"]):
        with caplog.at_level(logging.INFO):
            fetch_metadata_main()

    messages = [record.getMessage() for record in caplog.records]
    assert "Metadata fetch queue — to process: 1  skipped: 1" in messages
    assert "Metadata fetch complete — processed: 1  success: 1  no_abstract: 0  skipped: 1  errors: 0" in messages
    assert not any("skip (already fetched)" in message for message in messages)


def test_enrich_main_is_local_only_by_default(tmp_path, monkeypatch):
    papers_dir = tmp_path / "papers"
    extracted_dir = tmp_path / "extracted"
    papers_dir.mkdir()
    extracted_dir.mkdir()

    (papers_dir / "3764582.pdf").write_bytes(b"%PDF-1.4\n")
    (extracted_dir / "10_1145_3764582.json").write_text(
        json.dumps(
            {
                "filename": "10_1145_3764582.pdf",
                "stem": "10_1145_3764582",
                "pages_extracted": 0,
                "full_text": "",
                "abstract": None,
                "text_for_classification": "A Survey of Quantum Machine Learning",
                "bibliographic": {
                    "doi": "10.1145/3764582",
                    "title": "A Survey of Quantum Machine Learning",
                    "year": 2025,
                    "authors": ["A. Researcher"],
                    "venue": "ACM Computing Surveys",
                    "source": "semantic_scholar",
                },
                "ocr_used": False,
                "error": None,
            }
        ),
        encoding="utf-8",
    )

    call_kwargs = {}

    def fake_extract_text_from_pdf(*args, **kwargs):
        call_kwargs.update(kwargs)
        abstract = (
            "This survey reviews quantum machine learning foundations, algorithms, frameworks, "
            "datasets, and applications, with enough detail to count as a real abstract."
        )
        return {
            "filename": args[0].name,
            "stem": args[0].stem,
            "pages_extracted": 2,
            "full_text": abstract + " Full body.",
            "abstract": abstract,
            "text_for_classification": abstract,
            "bibliographic": {
                "doi": "10.1145/3764582",
                "title": "A Survey of Quantum Machine Learning",
                "year": 2025,
                "authors": ["A. Researcher"],
                "venue": "ACM Computing Surveys",
                "venue_type": None,
                "source": "heuristic",
            },
            "ocr_used": False,
            "error": None,
        }

    monkeypatch.setattr("enrich_from_pdfs.extract_text_from_pdf", fake_extract_text_from_pdf)
    enrich_main(
        [
            "--papers-dir",
            str(papers_dir),
            "--extracted-dir",
            str(extracted_dir),
            "--report-path",
            str(tmp_path / "report.json"),
            "--state-path",
            str(tmp_path / "state.json"),
        ]
    )

    assert call_kwargs["use_crossref"] is False
    assert call_kwargs["crossref_mailto"] is None
