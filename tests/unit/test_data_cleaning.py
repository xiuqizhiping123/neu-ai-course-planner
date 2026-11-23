import hashlib
import json
from pathlib import Path

from scripts.data_cleaning import clean_snapshot, parse_rule


def test_flat_rule_preserves_grade_and_concurrent_enrollment():
    raw = (
        "CSYE 6200 (may be taken concurrently) with a minimum grade of B- "
        "or INFO 5100 with a minimum grade of B- "
        "or INFO 5100 with a minimum grade of B-"
    )

    result = parse_rule([raw])

    assert result["status"] == "parsed"
    assert result["expression"] == {
        "op": "OR",
        "requirements": [
            {"course": "CSYE 6200", "min_grade": "B-", "allow_concurrent": True},
            {"course": "INFO 5100", "min_grade": "B-", "allow_concurrent": False},
        ],
    }


def test_ambiguous_rule_preserves_text_for_review():
    fragments = ["DAMG 6105 with a minimum grade of B ; (DAMG 6210 or INFO 6210)"]

    assert parse_rule(fragments) == {
        "status": "needs_review",
        "raw": fragments,
        "expression": None,
    }
    assert parse_rule([])["status"] == "none"


def test_clean_snapshot_joins_banner_faculty_by_term_and_crn(tmp_path: Path):
    files = []

    def save(source: str, filename: str, raw: str) -> None:
        content = raw.encode()
        path = tmp_path / filename
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        files.append({
            "source": source,
            "path": filename,
            "url": "https://example.invalid",
            "fetched_at": "2026-11-22T00:00:00Z",
            "sha256": hashlib.sha256(content).hexdigest(),
        })

    save("catalog", "catalog/info.html", """
        2026-2027 Edition
        <div class="courseblock"><p>INFO 5100. Application Engineering. (4 Hours)</p></div>
    """)
    save("catalog", "catalog/ms-ses-boston.html", """
        2026-2027 Edition
        <div id="programrequirementstextcontainer">
            <h2>Core Requirements</h2>
            <table><tr><td>INFO 5100</td><td>Application Engineering</td><td>4</td></tr></table>
            <h3>Coursework Option</h3>
            <p>Complete 8 semester hours from the restricted electives course list below. | 8</p>
            <p>Complete 12 semester hours from the other electives course list below. | 12</p>
            <h4>Restricted Electives</h4>
            <p>Complete restricted elective requirements from the following subject code:</p>
            <p>CSYE</p>
            <h4>Other Electives</h4>
            <p>Complete the remaining minimum program required semester hours.</p>
            <p>CSYE</p><p>INFO (INFO 6250 excluded)</p>
            <h2>Program Credit/GPA Requirements</h2><p>24 total semester hours required</p>
        </div>
    """)
    save("banner", "banner/INFO-00000.json", json.dumps({
        "success": True, "totalCount": 3, "pageOffset": 0,
        "data": [
            {
                "subject": "INFO", "courseNumber": "5100", "term": "202730",
                "courseReferenceNumber": crn, "campusDescription": campus,
                "faculty": [],
            }
            for crn, campus in (
                ("35409", "Boston"), ("35410", "Online"), ("35411", "Seattle")
            )
        ],
    }))
    save("banner", "banner/faculty-202730-35409.json", json.dumps({
        "fmt": [
            {
                "term": "202730", "courseReferenceNumber": "35409",
                "faculty": [{"displayName": "Example, Avery"}],
            }
            for _ in range(2)
        ],
    }))
    (tmp_path / "manifest.json").write_text(
        json.dumps({"complete": True, "files": files}), encoding="utf-8"
    )

    datasets = clean_snapshot(tmp_path, "Boston")
    sections = datasets["sections"]
    assert {section["crn"]: section["instructors"] for section in sections} == {
        "35409": ["Example, Avery"],
        "35410": [],
    }
    assert datasets["programs"][0]["electives"] == {
        "minimum_credits": 20,
        "minimum_credits_by_subject": {"CSYE": 8},
        "subjects": ["CSYE", "INFO"],
        "excluded_courses": ["INFO 6250"],
    }
    assert "### Coursework Option" in datasets["program_requirements.md"]
    assert datasets["review"] == []
