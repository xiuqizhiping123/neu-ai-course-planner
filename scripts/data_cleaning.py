"""Build offline review data from a complete fetch_neu_data.py snapshot."""

import argparse
import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

COURSE_CODE = r"[A-Z]{2,6} [0-9]{4}[A-Z]?"
NUMBER = r"[0-9]+(?:\.[0-9]+)?"
COURSE_TITLE = re.compile(
    rf"({COURSE_CODE})\. (.+?)\.? "
    rf"\(({NUMBER})(?:\s*[-–]\s*({NUMBER}))? Hours?\)\.?"
)
RULE_LABEL = re.compile(
    r"(Prerequisite|Corequisite)(?:\(s\)|s)?:\s*", re.IGNORECASE
)
RULE_LEAF = re.compile(
    rf"({COURSE_CODE})( \(may be taken concurrently\))?"
    r"(?: with a minimum grade of ([ABCDF][+-]?))?"
)
CATALOG_YEAR = re.compile(r"\b([0-9]{4}-[0-9]{4}) Edition\b")
BANNER_PAGE = re.compile(r"banner/([A-Z]+)-([0-9]+)\.json")
BANNER_FACULTY = re.compile(r"banner/faculty-([0-9]+)-([0-9]+)\.json")
PROGRAM_DOCUMENT = "program_requirements.md"
WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)


class CatalogHTML(HTMLParser):
    """Read paragraphs and table rows within CourseLeaf's content blocks."""

    def __init__(self, html: str) -> None:
        super().__init__()
        edition = CATALOG_YEAR.search(html)
        if edition is None:
            raise ValueError("Catalog edition was not found")
        self.year = edition[1]
        self.courses: list[list[tuple[str, str]]] = []
        self.program: list[tuple[str, str]] = []
        self._block: list[tuple[str, str]] | None = None
        self._depth = 0
        self._tag: str | None = None
        self._parts: list[str] = []
        self.feed(html)
        self.close()

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "div":
            if self._block is not None:
                self._depth += 1
            else:
                properties = dict(attrs)
                if "courseblock" in (properties.get("class") or "").split():
                    self._block = []
                    self.courses.append(self._block)
                elif properties.get("id") == "programrequirementstextcontainer":
                    self._block = self.program
                if self._block is not None:
                    self._depth = 1
        if self._block is None:
            return
        if self._tag is None and tag in {"p", "h2", "h3", "h4", "tr", "li"}:
            self._tag = tag
        elif self._tag is not None and tag == "br":
            self._parts.append(" ")
        elif self._tag == "tr" and tag in {"td", "th"}:
            self._parts.append(" | ")

    def handle_data(self, data: str) -> None:
        if self._tag is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._block is None:
            return
        if tag == self._tag:
            value = " ".join("".join(self._parts).split()).strip(" |")
            if value:
                self._block.append((tag, value))
            self._tag = None
            self._parts.clear()
        if tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self._block = None
                self._tag = None
                self._parts.clear()


def parse_rule(fragments: list[str]) -> dict[str, Any]:
    if not fragments:
        return {"status": "none", "raw": [], "expression": None}
    parts = re.split(r"\s+(and|or)\s+", fragments[0].rstrip("."), flags=re.IGNORECASE)
    operators = {part.upper() for part in parts[1::2]}
    pending = {"status": "needs_review", "raw": fragments, "expression": None}
    if len(fragments) > 1 or len(operators) > 1:
        return pending
    leaves: list[dict[str, Any]] = []
    for part in parts[::2]:
        match = RULE_LEAF.fullmatch(part)
        if match is None:
            return pending
        code, concurrent, grade = match.groups()
        leaf = {
            "course": code,
            "min_grade": grade,
            "allow_concurrent": bool(concurrent),
        }
        if leaf not in leaves:
            leaves.append(leaf)
    expression = leaves[0]
    if len(leaves) > 1:
        expression = {"op": operators.pop(), "requirements": leaves}
    return {"status": "parsed", "raw": fragments, "expression": expression}


def extract_courses(page: CatalogHTML, source: dict[str, Any]) -> list[dict[str, Any]]:
    if not page.courses:
        raise ValueError(f"No course blocks found in {source['path']}")
    courses: list[dict[str, Any]] = []
    for block in page.courses:
        if not block:
            raise ValueError(f"Empty course block in {source['path']}")
        match = COURSE_TITLE.fullmatch(block[0][1])
        if match is None:
            raise ValueError(f"Unrecognized course heading: {block[0][1]}")
        code, title, low, high = match.groups()
        descriptions: list[str] = []
        rules: dict[str, list[str]] = {"prerequisite": [], "corequisite": []}
        for _, paragraph in block[1:]:
            labels = list(RULE_LABEL.finditer(paragraph))
            if not labels:
                descriptions.append(paragraph)
            elif labels[0].start():
                descriptions.append(paragraph[:labels[0].start()].strip())
            for index, label in enumerate(labels):
                end = labels[index + 1].start() if index + 1 < len(labels) else None
                rules[label[1].lower()].append(paragraph[label.end():end].strip())
        courses.append({
            "course_code": code,
            "title": title,
            "catalog_year": page.year,
            "credits_min": float(low),
            "credits_max": float(high or low),
            "description": "\n\n".join(descriptions),
            "prerequisites": parse_rule(rules["prerequisite"]),
            "corequisites": parse_rule(rules["corequisite"]),
            "source": {**source, "record": code},
        })
    return courses


def extract_program(
    page: CatalogHTML, source: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    groups: dict[str, list[str]] = {}
    title = "Software Engineering Systems, MS (Boston)"
    document = [
        f"# {title}",
        f"{page.year} Academic Catalog",
        f"[Source]({source['url']})",
    ]
    heading = ""
    for tag, value in page.program:
        if tag in {"h2", "h3", "h4"}:
            heading = value
            document.append(f"{'#' * int(tag[1])} {value}")
        elif value != "Code | Title | Hours":
            groups.setdefault(heading, []).append(value)
            document.append(f"- {value}" if tag in {"tr", "li"} else value)

    keys = ("Core Requirements", "Coursework Option", "Program Credit/GPA Requirements")
    if not all(key in groups for key in keys):
        raise ValueError(f"Missing required program headings in {source['path']}")
    summary = " ".join(groups[keys[2]])
    credits = re.search(r"([0-9]+) total semester hours required", summary)
    gpa = re.search(rf"Minimum ({NUMBER}) GPA required", summary)
    core_grade = re.search(
        r"grade of ([ABCDF][+-]?) or higher in all required core courses",
        " ".join(groups[keys[0]]),
    )
    core = []
    for row in groups[keys[0]]:
        match = re.fullmatch(rf"({COURSE_CODE})\s*\|\s*(.+)\s*\|\s*({NUMBER})", row)
        if match:
            core.append({"course_code": match[1], "credits": float(match[3])})
    quotas = {}
    for row in groups[keys[1]]:
        quota = re.fullmatch(
            r"Complete ([0-9]+) semester hours from the (restricted|other) "
            r"electives course list below\. \| ([0-9]+)", row
        )
        if quota is None or quota[1] != quota[3] or quota[2] in quotas:
            raise ValueError(f"Unrecognized coursework requirement: {row}")
        quotas[quota[2]] = int(quota[1])

    restricted = groups.get("Restricted Electives", [])
    allowed = groups.get("Other Electives", [])
    if (
        not credits
        or not core
        or quotas.keys() != {"restricted", "other"}
        or len(restricted) != 2
        or not restricted[0].startswith("Complete restricted elective requirements")
        or not re.fullmatch(r"[A-Z]{2,6}", restricted[1])
        or not allowed
        or not allowed[0].startswith("Complete the remaining")
    ):
        raise ValueError(f"Unrecognized program requirements in {source['path']}")
    subjects = []
    exclusions = []
    for row in allowed[1:]:
        subject = re.fullmatch(
            rf"([A-Z]{{2,6}})(?: \(({COURSE_CODE}) excluded\))?", row
        )
        if subject is None:
            raise ValueError(f"Unrecognized elective subject: {row}")
        subjects.append(subject[1])
        if subject[2]:
            exclusions.append(subject[2])
    if restricted[1] not in subjects or any(
        code.startswith(f"{restricted[1]} ") for code in exclusions
    ):
        raise ValueError("Restricted electives must be a subset of eligible electives")
    required_credits = int(credits[1])
    elective_credits = sum(quotas.values())
    if sum(course["credits"] for course in core) + elective_credits != required_credits:
        raise ValueError("Core and elective credits do not add up")

    program = {
        "program_id": "ms-ses-boston",
        "campus": "Boston",
        "catalog_year": page.year,
        "option": "coursework",
        "total_required_credits": required_credits,
        "minimum_gpa": float(gpa[1]) if gpa else None,
        "minimum_core_grade": core_grade[1] if core_grade else None,
        "required_courses": core,
        "electives": {
            "minimum_credits": elective_credits,
            "minimum_credits_by_subject": {restricted[1]: quotas["restricted"]},
            "subjects": subjects,
            "excluded_courses": exclusions,
        },
        "document": PROGRAM_DOCUMENT,
        "source": {**source, "record": "#programrequirementstextcontainer"},
    }
    return program, "\n\n".join(document) + "\n"


def normalize_meeting(meeting: dict[str, Any]) -> dict[str, Any]:
    def normalize(value: str | None, fmt: str, output: str) -> str | None:
        if value is None or value.strip().upper() in {"", "TBA"}:
            return None
        if fmt == "%H%M" and not re.fullmatch(r"[0-9]{4}", value):
            raise ValueError(f"Invalid Banner meeting time: {value!r}")
        return datetime.strptime(value, fmt).strftime(output)

    return {
        "days": [day for day in WEEKDAYS if meeting.get(day) is True],
        "start_time": normalize(meeting.get("beginTime"), "%H%M", "%H:%M"),
        "end_time": normalize(meeting.get("endTime"), "%H%M", "%H:%M"),
        "start_date": normalize(meeting.get("startDate"), "%m/%d/%Y", "%Y-%m-%d"),
        "end_date": normalize(meeting.get("endDate"), "%m/%d/%Y", "%Y-%m-%d"),
        "campus": meeting.get("campusDescription"),
        "building": meeting.get("buildingDescription"),
        "room": meeting.get("room"),
        "type": meeting.get("meetingTypeDescription"),
    }


def faculty_names(faculty: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(
        person["displayName"] for person in faculty if person.get("displayName")
    ))


def extract_section(row: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    term = str(row["term"])
    crn = str(row["courseReferenceNumber"])
    code = f"{row['subject']} {row['courseNumber']}"
    if not term.isdigit() or not crn.isdigit() or not re.fullmatch(COURSE_CODE, code):
        raise ValueError(f"Invalid Banner section identity: {term} / {crn} / {code}")
    meetings = []
    faculty = list(row.get("faculty") or [])
    for entry in row.get("meetingsFaculty") or []:
        faculty.extend(entry.get("faculty") or [])
        if not entry.get("meetingTime"):
            continue
        try:
            meetings.append(normalize_meeting(entry["meetingTime"]))
        except ValueError as error:
            raise ValueError(f"CRN {crn} in {source['path']}: {error}") from error
    return {
        "term": term,
        "term_name": row.get("termDesc"),
        "crn": crn,
        "course_code": code,
        "section_number": row.get("sequenceNumber"),
        "campus": row.get("campusDescription"),
        "instructors": faculty_names(faculty),
        "instructional_method": row.get("instructionalMethodDescription"),
        "schedule_type": row.get("scheduleTypeDescription"),
        "credits": row.get("creditHours"),
        "credits_min": row.get("creditHourLow"),
        "credits_max": row.get("creditHourHigh"),
        "capacity": row.get("maximumEnrollment"),
        "seats_available": row.get("seatsAvailable"),
        "waitlist_available": row.get("waitAvailable"),
        "meetings": meetings,
        "source": {**source, "record": crn},
    }


def validate_pages(pages: dict[str, list[tuple[int, int, int]]]) -> None:
    for subject, entries in pages.items():
        offset = 0
        total = entries[0][1]
        for page_offset, page_total, size in sorted(entries):
            if page_offset != offset or page_total != total or not size:
                raise ValueError(f"Inconsistent Banner pagination for {subject}")
            offset += size
        if offset != total:
            raise ValueError(
                f"Incomplete Banner pagination for {subject}: {offset}/{total}"
            )


def review_items(
    courses: dict[str, dict[str, Any]],
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    review: list[dict[str, Any]] = []
    for code, course in courses.items():
        for key in ("prerequisites", "corequisites"):
            rule = course[key]
            references = set(re.findall(COURSE_CODE, " ".join(rule["raw"])))
            missing = sorted(references - courses.keys())
            if rule["status"] == "needs_review" or missing:
                review.append({
                    "kind": "course_requirements",
                    "course_code": code,
                    "field": key,
                    "status": rule["status"],
                    "raw": rule["raw"],
                    "missing_courses": missing,
                    "source": course["source"],
                })
    for section in sections:
        if section["course_code"] not in courses:
            review.append({
                "kind": "missing_section_course",
                "course_code": section["course_code"],
                "source": section["source"],
            })
    return review


def clean_snapshot(snapshot: Path, campus: str) -> dict[str, Any]:
    manifest = json.loads((snapshot / "manifest.json").read_bytes())
    if manifest.get("complete") is not True:
        raise ValueError("Snapshot is incomplete")
    courses: dict[str, dict[str, Any]] = {}
    sections: list[dict[str, Any]] = []
    faculty_by_section: dict[tuple[str, str], list[str]] = {}
    crns: set[tuple[str, str]] = set()
    page_counts: dict[str, list[tuple[int, int, int]]] = {}
    program = None
    program_document = ""
    for entry in sorted(manifest["files"], key=lambda item: item["path"]):
        path = entry["path"]
        match = BANNER_PAGE.fullmatch(path)
        faculty_match = BANNER_FACULTY.fullmatch(path)
        catalog = entry["source"] == "catalog" and path.endswith(".html")
        if not catalog and match is None and faculty_match is None:
            continue
        content = (snapshot / path).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"Snapshot checksum mismatch: {path}")
        source = {key: entry[key] for key in ("path", "url", "fetched_at", "sha256")}
        if catalog:
            page = CatalogHTML(content.decode("utf-8"))
            if path == "catalog/ms-ses-boston.html":
                program, program_document = extract_program(page, source)
            else:
                for course in extract_courses(page, source):
                    code = course["course_code"]
                    if code in courses:
                        raise ValueError(f"Duplicate catalog course: {code}")
                    courses[code] = course
        elif faculty_match is not None:
            term, crn = faculty_match.groups()
            detail = json.loads(content)
            meetings = detail.get("fmt")
            if not isinstance(meetings, list):
                raise ValueError(f"Missing Banner faculty results in {path}")
            faculty = []
            for meeting in meetings:
                meeting_identity = (
                    str(meeting["term"]), str(meeting["courseReferenceNumber"])
                )
                if meeting_identity != (term, crn):
                    raise ValueError(f"Banner faculty section does not match {path}")
                faculty.extend(meeting.get("faculty") or [])
            faculty_by_section[(term, crn)] = faculty_names(faculty)
        else:
            assert match is not None
            subject, offset = match.groups()
            payload = json.loads(content)
            rows = payload.get("data")
            total = payload.get("totalCount")
            if payload.get("success") is not True or not isinstance(rows, list):
                raise ValueError(f"Missing Banner results in {path}")
            if type(total) is not int or payload.get("pageOffset") != int(offset):
                raise ValueError(f"Invalid Banner pagination metadata in {path}")
            page_counts.setdefault(subject, []).append((int(offset), total, len(rows)))
            for row in rows:
                if row["subject"] != subject:
                    raise ValueError(f"Unexpected Banner subject in {path}")
                identity = (str(row["term"]), str(row["courseReferenceNumber"]))
                if identity in crns:
                    raise ValueError(f"Duplicate Banner section: {identity}")
                crns.add(identity)
                if (row.get("campusDescription") or "").casefold() in {
                    campus.casefold(), "online"
                }:
                    sections.append(extract_section(row, source))
    validate_pages(page_counts)
    if not courses or program is None or not sections:
        raise ValueError(
            f"Expected course, program and Banner section data for {campus}"
        )
    catalog_years = {course["catalog_year"] for course in courses.values()}
    if catalog_years != {program["catalog_year"]}:
        raise ValueError("Catalog files use inconsistent editions")
    if len({term for term, _ in crns}) != 1:
        raise ValueError("Banner pages use inconsistent terms")
    for section in sections:
        additional = faculty_by_section.get((section["term"], section["crn"]), [])
        section["instructors"] = list(dict.fromkeys(section["instructors"] + additional))
    sections.sort(key=lambda section: (section["course_code"], section["crn"]))
    return {
        "courses": [courses[code] for code in sorted(courses)],
        "sections": sections,
        "programs": [program],
        PROGRAM_DOCUMENT: program_document,
        "review": review_items(courses, sections),
    }


def write_results(output: Path, datasets: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temp:
        staging = Path(temp) / "results"
        staging.mkdir()
        for name, data in datasets.items():
            if name == PROGRAM_DOCUMENT:
                filename, payload = name, data
            else:
                filename = f"{name}.json"
                payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            (staging / filename).write_text(payload, encoding="utf-8", newline="\n")
        if output.exists():
            artifacts = list(staging.iterdir())
            if all((output / f.name).is_file() for f in artifacts) and all(
                (output / f.name).read_bytes() == f.read_bytes() for f in artifacts
            ):
                return
            raise FileExistsError(
                f"{output} already exists. Choose a new --output-dir."
            )
        staging.rename(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Directory containing manifest.json")
    parser.add_argument(
        "--output-dir", type=Path, help="Default: data/cleaned/<snapshot>"
    )
    args = parser.parse_args()
    output = args.output_dir or Path("data/cleaned") / args.snapshot.name
    try:
        datasets = clean_snapshot(args.snapshot, "Boston")
        write_results(output, datasets)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Cleaning failed: {error}\n")
    sizes = ", ".join(f"{len(data)} {name}" for name, data in datasets.items())
    print(f"{output}: {sizes}")


if __name__ == "__main__":
    main()
