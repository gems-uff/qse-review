"""Enrich existing extracted JSON files with data recovered from local PDFs."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from extract_text import MIN_ABSTRACT_LENGTH, extract_text_from_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PAPERS_DIR = Path("papers")
EXTRACTED_DIR = Path("out/extracted")
DOIS_PATH = Path("out/dois.json")
UNRESOLVED_PATH = Path("out/unresolved_papers.json")
REPORT_PATH = Path("out/pdf_enrichment_report.json")
STATE_PATH = Path("out/pdf_enrichment_state.json")

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_ACM_FILENAME_RE = re.compile(r"^\d+(?:\.\d+)?$")
_SPRINGER_FILENAME_RE = re.compile(r"^s\d{5}-\d{3}-\d{5}-\d$", re.IGNORECASE)


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    return _NORMALIZE_RE.sub("", value.lower())


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _public_record(record: dict) -> dict:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _pdf_fingerprint(pdf_path: Path) -> dict:
    stat = pdf_path.stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _extracted_snapshot(extracted_dir: Path) -> dict:
    files = sorted(path.name for path in extracted_dir.glob("*.json"))
    return {
        "count": len(files),
        "files": files,
    }


def _doi_from_filename(pdf_path: Path) -> str | None:
    stem = pdf_path.stem
    if _ACM_FILENAME_RE.fullmatch(stem):
        return f"10.1145/{stem}"
    if _SPRINGER_FILENAME_RE.fullmatch(stem):
        return f"10.1007/{stem.lower()}"
    return None


def _record_keys(record: dict) -> set[str]:
    keys: set[str] = set()
    bibliographic = record.get("bibliographic") or {}

    for raw_value in (
        bibliographic.get("title"),
        record.get("stem"),
        record.get("filename"),
    ):
        if not isinstance(raw_value, str):
            continue
        value = Path(raw_value).stem if raw_value.endswith(".pdf") else raw_value
        normalized = _normalize(value)
        if normalized:
            keys.add(normalized)

    return keys


def _build_index(extracted_dir: Path) -> tuple[list[dict], dict]:
    records: list[dict] = []
    doi_index: dict[str, list[dict]] = {}
    key_index: dict[str, list[dict]] = {}

    for path in sorted(extracted_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        record["_path"] = path
        records.append(record)

        doi = ((record.get("bibliographic") or {}).get("doi") or "").lower()
        if doi:
            doi_index.setdefault(doi, []).append(record)

        for key in _record_keys(record):
            key_index.setdefault(key, []).append(record)

    return records, {"doi": doi_index, "key": key_index}


def _current_target_for_pdf(pdf_path: Path, index: dict) -> dict | None:
    for records in index["doi"].values():
        for record in records:
            if (record.get("pdf_enrichment") or {}).get("source_pdf") == pdf_path.name:
                return record
    seen_paths: set[Path] = set()
    for records in index["key"].values():
        for record in records:
            if record["_path"] in seen_paths:
                continue
            seen_paths.add(record["_path"])
            if (record.get("pdf_enrichment") or {}).get("source_pdf") == pdf_path.name:
                return record
    return None


def _should_skip_pdf(
    pdf_path: Path,
    *,
    state: dict,
    index: dict,
    extracted_snapshot: dict,
    overwrite: bool,
) -> tuple[bool, str | None]:
    if overwrite:
        return False, None

    state_entry = state.get(pdf_path.name)
    if not state_entry:
        return False, None

    if state_entry.get("fingerprint") != _pdf_fingerprint(pdf_path):
        return False, None

    if state_entry.get("status") == "matched":
        record = _current_target_for_pdf(pdf_path, index)
        if (
            record
            and record["_path"].name == state_entry.get("target")
            and (record.get("pdf_enrichment") or {}).get("source_pdf") == pdf_path.name
        ):
            return True, "already enriched"
        return False, None

    if state_entry.get("status") in {"unmatched", "ambiguous"}:
        if state_entry.get("extracted_snapshot") == extracted_snapshot:
            return True, f"already {state_entry['status']}"

    return False, None


def _match_record(pdf_path: Path, pdf_result: dict, index: dict) -> tuple[dict | None, str, list[str]]:
    doi_candidates = [
        ((pdf_result.get("bibliographic") or {}).get("doi") or "").lower(),
        (_doi_from_filename(pdf_path) or "").lower(),
    ]
    for pdf_doi in [candidate for candidate in doi_candidates if candidate]:
        doi_matches = index["doi"].get(pdf_doi, [])
        if len(doi_matches) == 1:
            match_by = "doi" if pdf_doi == doi_candidates[0] else "filename-doi"
            return doi_matches[0], match_by, []
        if len(doi_matches) > 1:
            return None, "ambiguous-doi", [m["_path"].name for m in doi_matches]

    candidate_keys = {
        _normalize(pdf_path.stem),
        _normalize((pdf_result.get("bibliographic") or {}).get("title")),
    }
    candidate_keys = {key for key in candidate_keys if key}

    candidates: dict[Path, tuple[dict, str]] = {}
    for key in sorted(candidate_keys):
        for record in index["key"].get(key, []):
            match_by = "title" if key == _normalize((pdf_result.get("bibliographic") or {}).get("title")) else "stem"
            candidates[record["_path"]] = (record, match_by)

    if len(candidates) == 1:
        record, match_by = next(iter(candidates.values()))
        return record, match_by, []
    if len(candidates) > 1:
        return None, "ambiguous-key", [path.name for path in sorted(candidates)]
    return None, "unmatched", []


def _looks_like_title_only(text: str | None) -> bool:
    if not text:
        return True
    words = text.split()
    return len(words) <= 20 and "." not in text


def _merge_bibliographic(existing_bio: dict, pdf_bio: dict) -> tuple[dict, list[str], list[str]]:
    merged = deepcopy(existing_bio)
    updated_fields: list[str] = []
    conflicts: list[str] = []

    for field in ("doi", "title", "year", "authors", "venue", "venue_type"):
        existing_value = merged.get(field)
        pdf_value = pdf_bio.get(field)
        if not pdf_value:
            continue
        if existing_value in (None, "", []):
            merged[field] = pdf_value
            updated_fields.append(field)
            continue
        if field == "doi" and existing_value != pdf_value:
            conflicts.append(f"doi:{existing_value}->{pdf_value}")
            continue
        if (
            merged.get("source") == "spreadsheet"
            and pdf_bio.get("source") == "crossref"
            and field in {"title", "year", "authors", "venue", "venue_type"}
            and existing_value != pdf_value
        ):
            merged[field] = pdf_value
            updated_fields.append(field)

    if updated_fields and pdf_bio.get("source"):
        merged["source"] = pdf_bio.get("source")

    return merged, updated_fields, conflicts


def _merge_record(existing: dict, pdf_result: dict, pdf_path: Path, match_by: str) -> tuple[dict, dict]:
    merged = deepcopy(_public_record(existing))
    info = {
        "updated_fields": [],
        "conflicts": [],
        "abstract_recovered": False,
        "doi_recovered": False,
    }

    existing_abstract = merged.get("abstract")
    pdf_abstract = pdf_result.get("abstract")
    if pdf_abstract and (
        not existing_abstract or len(existing_abstract) < MIN_ABSTRACT_LENGTH
    ):
        merged["abstract"] = pdf_abstract
        merged["text_for_classification"] = pdf_abstract
        info["updated_fields"].extend(["abstract", "text_for_classification"])
        info["abstract_recovered"] = True

    pdf_text = pdf_result.get("full_text") or ""
    existing_text = merged.get("full_text") or ""
    if len(pdf_text.strip()) > len(existing_text.strip()):
        merged["full_text"] = pdf_text
        info["updated_fields"].append("full_text")

    if (
        not info["abstract_recovered"]
        and _looks_like_title_only(merged.get("text_for_classification"))
        and pdf_result.get("text_for_classification")
        and not _looks_like_title_only(pdf_result["text_for_classification"])
    ):
        merged["text_for_classification"] = pdf_result["text_for_classification"]
        info["updated_fields"].append("text_for_classification")

    merged["pages_extracted"] = max(
        int(merged.get("pages_extracted") or 0),
        int(pdf_result.get("pages_extracted") or 0),
    )
    merged["ocr_used"] = bool(merged.get("ocr_used")) or bool(pdf_result.get("ocr_used"))

    merged_bio, bio_updates, bio_conflicts = _merge_bibliographic(
        merged.get("bibliographic") or {},
        pdf_result.get("bibliographic") or {},
    )
    merged["bibliographic"] = merged_bio
    if bio_updates:
        info["updated_fields"].append("bibliographic")
    info["updated_fields"].extend(f"bibliographic.{field}" for field in bio_updates)
    info["conflicts"].extend(bio_conflicts)
    info["doi_recovered"] = "doi" in bio_updates

    merged["pdf_enrichment"] = {
        "source_pdf": pdf_path.name,
        "matched_by": match_by,
        "updated_fields": sorted(set(info["updated_fields"])),
        "conflicts": info["conflicts"],
        "abstract_recovered": info["abstract_recovered"],
        "doi_recovered": info["doi_recovered"],
        "bibliographic_source": (pdf_result.get("bibliographic") or {}).get("source"),
        "enriched_at": datetime.now(timezone.utc).isoformat(),
    }

    return merged, info


def _candidate_titles(record: dict, pdf_result: dict, pdf_path: Path) -> set[str]:
    titles: set[str] = set()
    for value in (
        (record.get("bibliographic") or {}).get("title"),
        (pdf_result.get("bibliographic") or {}).get("title"),
        pdf_path.stem,
    ):
        normalized = _normalize(value)
        if normalized:
            titles.add(normalized)
    return titles


def _apply_recovered_doi(dois: list[dict], recovered_doi: str, title_keys: set[str]) -> str | None:
    matches = [
        record
        for record in dois
        if not record.get("doi") and _normalize(record.get("title")) in title_keys
    ]
    if len(matches) != 1:
        return None

    matches[0]["doi"] = recovered_doi
    matches[0]["doi_source"] = "pdf_text"
    return matches[0]["title"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Enrich extracted JSON files with local PDFs.")
    parser.add_argument("--papers-dir", type=Path, default=PAPERS_DIR)
    parser.add_argument("--extracted-dir", type=Path, default=EXTRACTED_DIR)
    parser.add_argument("--dois-path", type=Path, default=DOIS_PATH)
    parser.add_argument("--unresolved-path", type=Path, default=UNRESOLVED_PATH)
    parser.add_argument("--report-path", type=Path, default=REPORT_PATH)
    parser.add_argument("--state-path", type=Path, default=STATE_PATH)
    parser.add_argument("--update-dois", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--mailto", default=os.environ.get("CROSSREF_MAILTO"))
    parser.add_argument(
        "--crossref",
        action="store_true",
        help="Allow CrossRef lookups while enriching PDFs. Disabled by default.",
    )
    args = parser.parse_args(argv)

    if not args.papers_dir.exists():
        logger.error("Papers directory not found: %s", args.papers_dir)
        sys.exit(1)
    if not args.extracted_dir.exists():
        logger.error("Extracted directory not found: %s", args.extracted_dir)
        sys.exit(1)

    pdf_files = sorted(args.papers_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning("No PDF files found in %s", args.papers_dir)
        sys.exit(0)

    mailto = args.mailto if args.crossref else None
    dois = _load_json(args.dois_path, [])
    state = _load_json(args.state_path, {})
    processed = 0
    skipped = 0
    enriched = 0
    ambiguous = 0
    unmatched = 0
    doi_updates = 0
    extracted_snapshot = _extracted_snapshot(args.extracted_dir)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "processed": [],
        "ambiguous": [],
        "unmatched": [],
        "doi_updates": [],
        "skipped": [],
    }

    records, index = _build_index(args.extracted_dir)
    del records  # only the index is needed directly

    for pdf_path in pdf_files:
        if args.limit is not None and processed >= args.limit:
            break
        skip, reason = _should_skip_pdf(
            pdf_path,
            state=state,
            index=index,
            extracted_snapshot=extracted_snapshot,
            overwrite=args.overwrite,
        )
        if skip:
            skipped += 1
            logger.info("  skip (%s): %s", reason, pdf_path.name)
            report["skipped"].append({"pdf": pdf_path.name, "reason": reason})
            continue
        processed += 1
        logger.info("[%d/%d] %s", processed, len(pdf_files), pdf_path.name)

        pdf_result = extract_text_from_pdf(
            pdf_path,
            use_ocr=args.ocr,
            crossref_mailto=mailto,
            use_crossref=args.crossref,
        )
        if pdf_result.get("error"):
            state[pdf_path.name] = {
                "status": "unmatched",
                "fingerprint": _pdf_fingerprint(pdf_path),
                "extracted_snapshot": extracted_snapshot,
            }
            report["unmatched"].append(
                {"pdf": pdf_path.name, "reason": f"extract_error:{pdf_result['error']}"}
            )
            unmatched += 1
            continue

        record, match_by, candidates = _match_record(pdf_path, pdf_result, index)
        if record is None:
            if match_by.startswith("ambiguous"):
                state[pdf_path.name] = {
                    "status": "ambiguous",
                    "fingerprint": _pdf_fingerprint(pdf_path),
                    "extracted_snapshot": extracted_snapshot,
                }
                ambiguous += 1
                report["ambiguous"].append(
                    {"pdf": pdf_path.name, "match_by": match_by, "candidates": candidates}
                )
            else:
                state[pdf_path.name] = {
                    "status": "unmatched",
                    "fingerprint": _pdf_fingerprint(pdf_path),
                    "extracted_snapshot": extracted_snapshot,
                }
                unmatched += 1
                report["unmatched"].append({"pdf": pdf_path.name, "reason": match_by})
            continue

        if (
            not args.overwrite
            and (record.get("pdf_enrichment") or {}).get("source_pdf") == pdf_path.name
            and record.get("abstract")
            and len(record.get("abstract") or "") >= MIN_ABSTRACT_LENGTH
        ):
            logger.info("  skip (already enriched): %s", pdf_path.name)
            state[pdf_path.name] = {
                "status": "matched",
                "target": record["_path"].name,
                "fingerprint": _pdf_fingerprint(pdf_path),
                "extracted_snapshot": extracted_snapshot,
            }
            skipped += 1
            report["skipped"].append({"pdf": pdf_path.name, "reason": "already enriched"})
            continue

        merged, info = _merge_record(record, pdf_result, pdf_path, match_by)
        if merged == _public_record(record) and not info["conflicts"]:
            logger.info("  no changes: %s", record["_path"].name)
        else:
            record["_path"].write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            enriched += 1

        report["processed"].append(
            {
                "pdf": pdf_path.name,
                "target": record["_path"].name,
                "matched_by": match_by,
                "updated_fields": sorted(set(info["updated_fields"])),
                "conflicts": info["conflicts"],
            }
        )

        recovered_doi = (merged.get("bibliographic") or {}).get("doi")
        if args.update_dois and recovered_doi:
            updated_title = _apply_recovered_doi(
                dois,
                recovered_doi,
                _candidate_titles(merged, pdf_result, pdf_path),
            )
            if updated_title:
                doi_updates += 1
                report["doi_updates"].append(
                    {"pdf": pdf_path.name, "title": updated_title, "doi": recovered_doi}
                )

        _, index = _build_index(args.extracted_dir)
        state[pdf_path.name] = {
            "status": "matched",
            "target": record["_path"].name,
            "fingerprint": _pdf_fingerprint(pdf_path),
            "extracted_snapshot": extracted_snapshot,
        }

    if args.update_dois and args.dois_path:
        args.dois_path.write_text(
            json.dumps(dois, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        args.unresolved_path.write_text(
            json.dumps([record for record in dois if not record.get("doi")], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.state_path.parent.mkdir(parents=True, exist_ok=True)
    args.state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "PDF enrichment complete — enriched: %d  skipped: %d  ambiguous: %d  unmatched: %d  doi_updates: %d",
        enriched,
        skipped,
        ambiguous,
        unmatched,
        doi_updates,
    )


if __name__ == "__main__":
    main()
