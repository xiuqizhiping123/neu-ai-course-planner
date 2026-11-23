import hashlib
import json
from pathlib import Path

import pytest

from course_planner.catalog_data import load_runtime_data
from scripts.data_pipeline import overlay_snapshots, publish


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_partial_banner_refresh_replaces_subject_pages_and_faculty(tmp_path: Path):
    info = {"term": "202730", "courseReferenceNumber": "12345"}
    damg = {"term": "202730", "courseReferenceNumber": "23456"}
    refreshed = {"term": "202730", "courseReferenceNumber": "34567"}
    base = tmp_path / "base"
    refresh = tmp_path / "refresh"

    def snapshot(directory: Path, files: dict[str, object]) -> None:
        entries = []
        for name, value in files.items():
            path = directory / name
            write_json(path, value)
            entries.append({
                "path": name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        write_json(directory / "manifest.json", {
            "term": "2027301", "complete": True, "files": entries,
        })

    snapshot(base, {
        "catalog/info.html": "Catalog",
        "banner/INFO-00000.json": {"data": []},
        "banner/INFO-00100.json": {"data": [info]},
        "banner/faculty-202730-12345.json": {"fmt": []},
        "banner/DAMG-00000.json": {"data": [damg]},
        "banner/faculty-202730-23456.json": {"fmt": []},
    })
    snapshot(refresh, {
        "banner/INFO-00000.json": {"data": [refreshed]},
        "banner/faculty-202730-34567.json": {"fmt": []},
    })

    target = tmp_path / "merged"
    inputs = overlay_snapshots([base, refresh], target)

    assert inputs == {
        "catalog/info.html": "base",
        "banner/DAMG-00000.json": "base",
        "banner/faculty-202730-23456.json": "base",
        "banner/INFO-00000.json": "refresh",
        "banner/faculty-202730-34567.json": "refresh",
    }
    assert not (target / "banner/INFO-00100.json").exists()


def test_publish_checks_active_dependencies_and_preserves_prior_release(tmp_path: Path):
    draft, runtime = tmp_path / "draft", tmp_path / "runtime"
    corrections = tmp_path / "corrections.json"
    leaf = {"course": "INFO 6210", "min_grade": "B", "allow_concurrent": False}
    none = {"status": "none", "raw": [], "expression": None}
    pending = {"status": "needs_review", "raw": ["Complex rule"], "expression": None}
    active = {
        "course_code": "CSYE 6200", "prerequisites": pending, "corequisites": none,
    }
    unrelated = {**active, "course_code": "TELE 6350"}
    write_json(draft / "courses.json", [active, unrelated])
    write_json(draft / "sections.json", [{"course_code": "CSYE 6200"}])
    write_json(draft / "programs.json", [{
        "program_id": "ms-ses-boston",
        "total_required_credits": 24,
        "required_courses": [{"course_code": "CSYE 6200", "credits": 4}],
        "electives": {
            "minimum_credits": 20,
            "minimum_credits_by_subject": {"CSYE": 8},
            "subjects": ["CSYE", "INFO"],
        },
        "document": "program_requirements.md",
    }])
    (draft / "program_requirements.md").write_text(
        "# Program requirements\n", encoding="utf-8"
    )
    write_json(draft / "inputs.json", {})
    patch = {
        "course_rules": [{
            "course_code": "CSYE 6200", "field": "prerequisites",
            "expected_raw": pending["raw"], "expression": leaf, "reason": "Reviewed",
        }],
        "external_courses": [{
            "course_code": "INFO 6210", "evidence": "Archived catalog",
            "reason": "Historical transcript course",
        }],
    }
    write_json(corrections, patch)

    published = publish(draft, corrections, runtime)
    data = load_runtime_data(runtime)

    assert [item["course_code"] for item in data["courses"]] == ["CSYE 6200"]
    assert data["courses"][0]["prerequisites"]["expression"] == leaf
    assert data["external_courses"] == patch["external_courses"]
    assert (published / "program_requirements.md").is_file()
    assert publish(draft, corrections, runtime) == published
    with pytest.raises(ValueError, match="CSYE 6200: unresolved prerequisites"):
        publish(draft, None, runtime)
    assert load_runtime_data(runtime) == data
