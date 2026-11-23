"""Prepare reviewable data from raw snapshots and publish an approved release."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from typing import Any

from scripts.data_cleaning import (
    BANNER_FACULTY,
    BANNER_PAGE,
    COURSE_CODE,
    PROGRAM_DOCUMENT,
    clean_snapshot,
    write_results,
)
from scripts.fetch_neu_data import BANNER_TERM_IDS

DATASETS = ("courses", "sections", "programs")
RULE_FIELDS = ("prerequisites", "corequisites")


def read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def overlay_snapshots(snapshots: list[Path], target: Path) -> dict[str, str]:
    """Replace complete Banner subject batches and overlay other files by path."""
    selected: dict[str, tuple[Path, dict[str, Any], str | None]] = {}
    term = None
    for snapshot in snapshots:
        manifest = read_json(snapshot / "manifest.json")
        if manifest.get("complete") is not True:
            raise ValueError(f"Incomplete snapshot: {snapshot}")
        current_term = manifest["term"]
        if term is None:
            term = current_term
        banner_term = BANNER_TERM_IDS.get(current_term, current_term)
        if BANNER_TERM_IDS.get(term, term) != banner_term:
            raise ValueError(f"Conflicting terms in snapshot: {snapshot}")
        subjects: set[str] = set()
        crn_subjects: dict[tuple[str, str], str] = {}
        for entry in manifest["files"]:
            path = entry["path"]
            match = BANNER_PAGE.fullmatch(path)
            if match:
                subject = match[1]
                subjects.add(subject)
                for row in read_json(snapshot / path)["data"]:
                    key = (str(row["term"]), str(row["courseReferenceNumber"]))
                    crn_subjects[key] = subject
        selected = {
            path: item for path, item in selected.items() if item[2] not in subjects
        }
        for entry in manifest["files"]:
            path = entry["path"]
            page = BANNER_PAGE.fullmatch(path)
            faculty = BANNER_FACULTY.fullmatch(path)
            subject = page[1] if page else None
            if faculty:
                subject = crn_subjects.get((faculty[1], faculty[2]))
                if subject is None:
                    raise ValueError(f"Unmatched faculty details: {snapshot / path}")
            elif path.startswith("banner/") and path.endswith("-term.json"):
                subject = Path(path).name.removesuffix("-term.json")
            selected[path] = (snapshot, entry, subject)

    inputs = {}
    for path, (snapshot, entry, _) in selected.items():
        source = snapshot / path
        if hashlib.sha256(source.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Snapshot checksum mismatch: {source}")
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        copyfile(source, destination)
        inputs[path] = snapshot.name
    manifest = {"complete": True, "files": [entry for _, entry, _ in selected.values()]}
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return inputs


def correction_template(datasets: dict[str, Any]) -> dict[str, Any]:
    courses = {row["course_code"]: row for row in datasets["courses"]}
    _, _, issues = publication_scope(datasets, set())
    blocked = {code for code, _ in issues}
    rules = []
    missing = set()
    for issue in datasets["review"]:
        code = issue.get("course_code")
        if code not in blocked:
            continue
        if issue["kind"] == "course_requirements":
            missing.update(issue["missing_courses"])
            field = issue["field"]
            if courses[code][field]["status"] == "needs_review":
                rules.append({
                    "course_code": code,
                    "field": field,
                    "expected_raw": issue["raw"],
                    "expression": None,
                    "reason": "",
                })
    return {
        "course_rules": rules,
        "external_courses": [
            {"course_code": code, "evidence": "", "reason": ""}
            for code in sorted(missing - courses.keys())
        ],
    }


def expression_courses(expression: Any) -> set[str]:
    if expression is None:
        return set()
    if not isinstance(expression, dict):
        raise TypeError("A course rule must contain an expression object")
    if set(expression) == {"op", "requirements"}:
        children = expression["requirements"]
        if expression["op"] not in ("AND", "OR") or not isinstance(children, list):
            raise ValueError("Invalid course rule operator or requirements list")
        if len(children) < 2 or any(child is None for child in children):
            raise ValueError("A course rule group requires at least two expressions")
        return set().union(*(expression_courses(child) for child in children))
    if set(expression) != {"course", "min_grade", "allow_concurrent"}:
        raise ValueError("Rule leaf must contain course, min_grade and allow_concurrent")
    code = expression["course"]
    grade = expression["min_grade"]
    if not isinstance(code, str) or not re.fullmatch(COURSE_CODE, code):
        raise ValueError(f"Invalid course code in rule: {code!r}")
    if grade is not None and (
        not isinstance(grade, str) or not re.fullmatch(r"[ABCDF][+-]?", grade)
    ):
        raise ValueError(f"Invalid minimum grade in rule: {grade!r}")
    if not isinstance(expression["allow_concurrent"], bool):
        raise TypeError("allow_concurrent must be a boolean")
    return {code}


def publication_scope(
    datasets: dict[str, Any], external: set[str]
) -> tuple[set[str], set[str], list[tuple[str, str]]]:
    courses = {row["course_code"]: row for row in datasets["courses"]}
    required = {row["course_code"] for row in datasets["sections"]}
    for program in datasets["programs"]:
        required.update(course["course_code"] for course in program["required_courses"])
    pending = list(required)
    included: set[str] = set()
    used_external: set[str] = set()
    issues: set[tuple[str, str]] = set()
    while pending:
        code = pending.pop()
        if code in included:
            continue
        course = courses.get(code)
        if course is None:
            issues.add((code, "missing course record"))
            continue
        included.add(code)
        for field in RULE_FIELDS:
            rule = course[field]
            if rule["status"] not in {"none", "parsed"}:
                issues.add((code, f"unresolved {field}"))
                continue
            for reference in expression_courses(rule["expression"]):
                if reference in external and reference not in required:
                    used_external.add(reference)
                elif reference not in courses:
                    issues.add((code, f"{field} references missing {reference}"))
                else:
                    pending.append(reference)
    return included, used_external, sorted(issues)


def apply_corrections(
    datasets: dict[str, Any], corrections: dict[str, Any]
) -> dict[str, dict[str, str]]:
    allowed = {"course_rules", "external_courses"}
    if corrections.keys() - allowed:
        raise ValueError("Unknown corrections field")
    courses = {row["course_code"]: row for row in datasets["courses"]}
    seen: set[tuple[str, str]] = set()
    for change in corrections.get("course_rules", []):
        code, field = change["course_code"], change["field"]
        if field not in RULE_FIELDS or (code, field) in seen:
            raise ValueError(f"Invalid or duplicate correction: {code} {field}")
        seen.add((code, field))
        rule = courses[code][field]
        if rule["raw"] != change["expected_raw"]:
            raise ValueError(f"Rule source has changed: {code} {field}")
        if not change["reason"].strip():
            raise ValueError(f"A review reason is required: {code} {field}")
        expression = change["expression"]
        expression_courses(expression)
        rule.update(
            status="none" if expression is None else "parsed", expression=expression
        )

    external = {}
    for item in corrections.get("external_courses", []):
        code = item["course_code"]
        if code in courses or code in external or not re.fullmatch(COURSE_CODE, code):
            raise ValueError(f"Invalid or duplicate external course: {code}")
        if not item["reason"].strip() or not item["evidence"].strip():
            raise ValueError(f"Supply evidence and reason for external course: {code}")
        external[code] = item
    return external


def prepare(snapshots: list[Path], output: Path) -> None:
    with TemporaryDirectory() as temp:
        merged = Path(temp)
        inputs = overlay_snapshots(snapshots, merged)
        datasets = clean_snapshot(merged, "Boston")
    datasets["inputs"] = inputs
    datasets["corrections_template"] = correction_template(datasets)
    write_results(output, datasets)
    print(f"Draft ready: {output}")


def publish(draft: Path, corrections_path: Path | None, output: Path) -> Path:
    datasets = {name: read_json(draft / f"{name}.json") for name in DATASETS}
    if not all(isinstance(rows, list) and rows for rows in datasets.values()):
        raise ValueError("Draft must contain courses, sections and programs")
    if any("electives" not in row for row in datasets["programs"]):
        raise ValueError("Draft uses the old program format. Rerun prepare.")
    datasets[PROGRAM_DOCUMENT] = (draft / PROGRAM_DOCUMENT).read_text(encoding="utf-8")
    corrections = read_json(corrections_path) if corrections_path is not None else {}
    # Earlier drafts asked for manual program approval. That field is now obsolete.
    corrections.pop("programs", None)
    external = apply_corrections(datasets, corrections)
    included, used_external, issues = publication_scope(datasets, set(external))
    blockers = [f"{code}: {message}" for code, message in issues]
    for program in datasets["programs"]:
        program_id = program["program_id"]
        electives = program["electives"]
        total = program["total_required_credits"]
        minimum = electives["minimum_credits"]
        subject_requirements = electives["minimum_credits_by_subject"]
        if (
            not total
            or not program["required_courses"]
            or not minimum
            or not subject_requirements
            or not subject_requirements.keys() <= set(electives["subjects"])
            or any(credits <= 0 for credits in subject_requirements.values())
            or sum(subject_requirements.values()) > minimum
            or program["document"] != PROGRAM_DOCUMENT
            or not datasets[PROGRAM_DOCUMENT].strip()
        ):
            blockers.append(f"{program_id}: incomplete program requirements")
            continue
        core_credits = sum(course["credits"] for course in program["required_courses"])
        if core_credits + minimum != total:
            blockers.append(f"{program_id}: core and elective credits do not add up")
    if blockers:
        raise ValueError("Publication blocked:\n  " + "\n  ".join(blockers))
    datasets["courses"] = [
        row for row in datasets["courses"] if row["course_code"] in included
    ]
    datasets["external_courses"] = [external[code] for code in sorted(used_external)]
    datasets["publication"] = {
        "inputs": read_json(draft / "inputs.json"),
        "corrections": corrections,
    }
    payload = json.dumps(datasets, sort_keys=True).encode()
    release_id = hashlib.sha256(payload).hexdigest()
    release = output / "releases" / release_id
    write_results(release, datasets)
    with TemporaryDirectory(dir=output) as temp:
        pointer = Path(temp) / "current.json"
        pointer.write_text(json.dumps({"release": release_id}) + "\n", encoding="utf-8")
        pointer.replace(output / "current.json")
    return release


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True, dest="command")
    draft = commands.add_parser("prepare", help="Merge snapshots and write review data")
    draft.add_argument("--base", type=Path, required=True)
    draft.add_argument("--refresh", type=Path, action="append", default=[])
    draft.add_argument("--output-dir", type=Path, required=True)
    release = commands.add_parser("publish", help="Apply corrections and publish")
    release.add_argument("draft", type=Path)
    release.add_argument("--corrections", type=Path)
    release.add_argument("--output-dir", type=Path, default=Path("data/runtime"))
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare([args.base, *args.refresh], args.output_dir)
        else:
            location = publish(args.draft, args.corrections, args.output_dir)
            print(f"Published: {location}")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
