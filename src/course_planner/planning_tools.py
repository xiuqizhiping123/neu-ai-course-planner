"""Deterministic tools for the published Boston MS-SES Coursework catalog."""

from collections import Counter, defaultdict
from datetime import date
from typing import Any

from course_planner.catalog_data import load_runtime_data

DEFAULT_PROGRAM = "ms-ses-boston"
GRADE_RANK = {
    grade: index
    for index, grade in enumerate(
        ("F", "D-", "D", "D+", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+")
    )
}
WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)


def _code(value: str) -> str:
    return " ".join(value.upper().split())


def _transcript(
    completed_courses: list[str], grades: dict[str, str] | None
) -> dict[str, str | None]:
    normalized_grades = {
        _code(code): grade.strip().upper() for code, grade in (grades or {}).items()
    }
    codes = map(_code, completed_courses)
    return {code: normalized_grades.get(code) for code in codes}


def _program(data: dict[str, Any], program_id: str) -> dict[str, Any]:
    for program in data["programs"]:
        if program["program_id"] == program_id.strip().lower():
            return program
    raise ValueError(f"Unknown program: {program_id}")


def _credit_range(
    course: dict[str, Any], section: dict[str, Any] | None = None
) -> tuple[float, float]:
    if section is None:
        return course["credits_min"], course["credits_max"]
    if section["credits"] is not None:
        return section["credits"], section["credits"]
    low, high = section["credits_min"], section["credits_max"]
    if low is None:
        low = course["credits_min"]
    if high is None:
        high = max(low, course["credits_max"])
    return low, high


def _sections(data: dict[str, Any], program: dict[str, Any]) -> list[dict[str, Any]]:
    campus = program["campus"].casefold()
    return [
        row for row in data["sections"]
        if row["campus"].casefold() in {campus, "online"}
    ]


def get_available_courses(program_id: str = DEFAULT_PROGRAM) -> dict[str, Any]:
    """List published course rules and Boston or Online sections with snapshot seats."""
    data = load_runtime_data()
    program = _program(data, program_id)
    courses = {row["course_code"]: row for row in data["courses"]}
    sections = []
    for row in _sections(data, program):
        low, high = _credit_range(courses[row["course_code"]], row)
        sections.append({**row, "credits_min": low, "credits_max": high})
    return {"program": program, "courses": data["courses"], "sections": sections}


def _evaluate(
    expression: dict[str, Any],
    transcript: dict[str, str | None],
    planned: set[str],
    corequisite: bool,
) -> dict[str, Any]:
    if "op" in expression:
        children = [
            _evaluate(child, transcript, planned, corequisite)
            for child in expression["requirements"]
        ]
        outcomes = [child["eligible"] for child in children]
        if expression["op"] == "AND":
            eligible = False if False in outcomes else (None if None in outcomes else True)
        else:
            eligible = True if True in outcomes else None if None in outcomes else False
        return {"op": expression["op"], "eligible": eligible, "requirements": children}

    course, minimum = expression["course"], expression["min_grade"]
    grade = transcript.get(course)
    if (
        course in transcript
        and grade != "F"
        and (
            minimum is None
            or (grade in GRADE_RANK and GRADE_RANK[grade] >= GRADE_RANK[minimum])
        )
    ):
        return {**expression, "eligible": True, "status": "completed"}
    if course in planned and (corequisite or expression["allow_concurrent"]):
        return {**expression, "eligible": True, "status": "concurrent"}
    if course in transcript and grade not in GRADE_RANK and minimum is not None:
        return {**expression, "eligible": None, "status": "grade_required"}
    status = "grade_too_low" if course in transcript else "missing_course"
    return {**expression, "eligible": False, "status": status}


def _leaves(result: dict[str, Any]) -> list[dict[str, Any]]:
    if "requirements" not in result:
        return [result] if "course" in result else []
    children = result["requirements"]
    if result["op"] == "OR" and result["eligible"] is not False:
        eligible = result["eligible"]
        children = [child for child in children if child["eligible"] == eligible]
    return [leaf for child in children for leaf in _leaves(child)]


def _check(
    course: dict[str, Any],
    transcript: dict[str, str | None],
    planned: set[str],
) -> dict[str, Any]:
    rules = {}
    for key in ("prerequisites", "corequisites"):
        rule = course[key]
        if rule["status"] == "none":
            rules[key] = {"eligible": True}
        elif rule["status"] == "parsed":
            rules[key] = _evaluate(
                rule["expression"], transcript, planned, key == "corequisites"
            )
        else:
            rules[key] = {
                "eligible": None, "status": "needs_review", "raw": rule["raw"],
            }
    outcomes = [rule["eligible"] for rule in rules.values()]
    eligible = False if False in outcomes else None if None in outcomes else True
    leaves = [leaf for rule in rules.values() for leaf in _leaves(rule)]
    return {
        "eligible": eligible,
        "reason": {
            True: "Course requirements are satisfied or permitted concurrently.",
            False: "One or more course requirements are unmet.",
            None: "More information is needed to verify course requirements.",
        }[eligible],
        "satisfied": sorted({
            leaf["course"] for leaf in leaves if leaf["eligible"] is True
        }),
        "missing": sorted({
            leaf["course"] for leaf in leaves if leaf["eligible"] is not True
        }),
        "rules": rules,
    }


def check_prerequisites(
    completed_courses: list[str],
    target_course: str,
    grades: dict[str, str] | None = None,
    planned_courses: list[str] | None = None,
) -> dict[str, Any]:
    """Check prerequisites and corequisites with optional grades by course code."""
    data = load_runtime_data()
    code = _code(target_course)
    transcript = _transcript(completed_courses, grades)
    planned = {_code(course) for course in planned_courses or []} - {code}
    for course in data["courses"]:
        if course["course_code"] == code:
            return _check(course, transcript, planned)
    return {
        "eligible": None, "satisfied": [], "missing": [],
        "reason": f"No published course rules for {code}.", "rules": {},
    }


def _timed(meeting: dict[str, Any]) -> bool:
    return bool(meeting["days"] and meeting["start_time"] and meeting["end_time"])


def _conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if not _timed(left) or not _timed(right):
        return False
    days = set(left["days"]) & set(right["days"])
    if not days or max(left["start_time"], right["start_time"]) >= min(
        left["end_time"], right["end_time"]
    ):
        return False
    starts = [row["start_date"] for row in (left, right) if row["start_date"]]
    ends = [row["end_date"] for row in (left, right) if row["end_date"]]
    if not starts or not ends:
        return True
    start, end = date.fromisoformat(max(starts)), date.fromisoformat(min(ends))
    span = (end - start).days
    return any((WEEKDAYS.index(day) - start.weekday()) % 7 <= span for day in days)


def _schedule(
    choices: list[list[dict[str, Any]]],
    chosen: list[dict[str, Any]],
    courses: dict[str, dict[str, Any]],
    remaining_credits: float | None = None,
) -> list[dict[str, Any]] | None:
    if not choices:
        return chosen
    for section in choices[0]:
        _, maximum = _credit_range(courses[section["course_code"]], section)
        if remaining_credits is not None and maximum > remaining_credits:
            continue
        if any(
            _conflict(left, right)
            for other in chosen
            for left in section["meetings"] for right in other["meetings"]
        ):
            continue
        budget = remaining_credits - maximum if remaining_credits is not None else None
        result = _schedule(choices[1:], [*chosen, section], courses, budget)
        if result is not None:
            return result
    return None


def _degree_progress(
    program: dict[str, Any], courses: dict[str, dict[str, Any]],
    transcript: dict[str, str | None], selected: list[dict[str, Any]],
) -> dict[str, Any]:
    core = {row["course_code"] for row in program["required_courses"]}
    electives = program["electives"]
    core_grade = program["minimum_core_grade"]
    sections = {row["course_code"]: row for row in selected}
    subject_credits: dict[str, float] = defaultdict(float)
    core_credits = 0.0
    pending_core_grades = []
    missing_core = []
    unverified_transcript = []
    for code in sorted(core | transcript.keys() | sections.keys()):
        grade = transcript.get(code)
        planned = code in sections
        passed = code in transcript and grade != "F"
        if code in core:
            meets_grade = core_grade is None or GRADE_RANK.get(grade or "", -1) >= (
                GRADE_RANK[core_grade]
            )
            if passed and core_grade and grade not in GRADE_RANK and not planned:
                pending_core_grades.append(code)
            if not planned and (not passed or not meets_grade):
                missing_core.append(code)
                continue
            core_credits += _credit_range(courses[code], sections.get(code))[0]
        elif passed or planned:
            if code not in courses:
                unverified_transcript.append(code)
                continue
            subject = code.split()[0]
            if (
                subject in electives["subjects"]
                and code not in electives["excluded_courses"]
            ):
                minimum, _ = _credit_range(courses[code], sections.get(code))
                subject_credits[subject] += minimum
    elective_credits = sum(subject_credits.values())
    return {
        "missing_core_courses": missing_core,
        "core_grades_needed": pending_core_grades,
        "unverified_transcript_courses": unverified_transcript,
        "minimum_core_grade": core_grade,
        "minimum_gpa": program["minimum_gpa"],
        "remaining_total_credits": max(
            0, program["total_required_credits"] - core_credits - elective_credits
        ),
        "remaining_elective_credits": max(
            0, electives["minimum_credits"] - elective_credits
        ),
        "remaining_elective_credits_by_subject": {
            subject: max(0, minimum - subject_credits[subject])
            for subject, minimum in electives["minimum_credits_by_subject"].items()
        },
    }


def validate_course_plan(
    student_profile: dict[str, Any],
    proposed_courses: list[str],
    section_crns: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Check one term and report projected degree progress.

    The profile accepts completed_courses, grades, max_credits, and program_id.
    Optional section_crns pins specific sections by course code. Seat counts
    describe the snapshot. Individual enrollment policies require separate review.
    """
    data = load_runtime_data()
    program = _program(data, student_profile.get("program_id", DEFAULT_PROGRAM))
    courses = {row["course_code"]: row for row in data["courses"]}
    transcript = _transcript(
        student_profile.get("completed_courses", []), student_profile.get("grades")
    )
    counts = Counter(_code(course) for course in proposed_courses)
    pinned = {_code(code): crn for code, crn in (section_crns or {}).items()}
    cap = student_profile.get("max_credits")
    if cap is not None and (not isinstance(cap, (int, float)) or not cap >= 0):
        raise ValueError("max_credits must be a nonnegative number")
    issues = []
    warnings = []
    checks = {}
    options = []
    sections = _sections(data, program)
    for code in sorted(pinned.keys() - counts.keys()):
        issues.append(f"{code}: section specified for a course outside the plan.")
    for code, count in counts.items():
        if count > 1:
            issues.append(f"{code}: duplicate course in the plan.")
        if code in transcript and transcript[code] != "F":
            grade = transcript[code]
            is_core = any(
                row["course_code"] == code for row in program["required_courses"]
            )
            minimum = program["minimum_core_grade"]
            retaking_core = (
                is_core and minimum is not None and grade in GRADE_RANK
                and GRADE_RANK[grade] < GRADE_RANK[minimum]
            )
            if not retaking_core:
                issues.append(f"{code}: already completed.")
        if code not in courses:
            issues.append(f"{code}: no published course rules.")
            continue
        check = _check(courses[code], transcript, set(counts) - {code})
        checks[code] = check
        if check["eligible"] is not True:
            issues.append(f"{code}: {check['reason']}")
        available = [
            row for row in sections if row["course_code"] == code
            and (code not in pinned or row["crn"] == pinned[code])
        ]
        if not available:
            detail = f"no matching section ({pinned[code]})" if code in pinned else "no section"
            issues.append(f"{code}: {detail} in the published term.")
        options.append(available)

    selected = None
    if len(options) == len(counts) and all(options):
        selected = _schedule(options, [], courses, cap)
        if selected is None and cap is not None:
            selected = _schedule(options, [], courses)
        if selected is None:
            issues.append("No section combination avoids time conflicts.")
    planned = selected or []
    low = high = None
    if selected is not None and len(planned) == len(counts):
        ranges = [_credit_range(courses[row["course_code"]], row) for row in planned]
        low, high = sum(value[0] for value in ranges), sum(value[1] for value in ranges)
        if cap is not None and low > cap:
            issues.append(f"Minimum planned credits ({low:g}) exceed the {cap:g} limit.")
        elif cap is not None and high > cap:
            issues.append(f"Confirm variable credits within the {cap:g}-credit limit.")
    for row in planned:
        meetings = row["meetings"]
        if not meetings or not all(map(_timed, meetings)):
            warnings.append(f"{row['crn']}: at least one meeting time is unavailable.")
    return {
        "valid": not issues,
        "issues": issues,
        "warnings": warnings,
        "schedule_complete": selected is not None and not warnings,
        "checks": checks,
        "sections": planned,
        "credits_min": low,
        "credits_max": high,
        "graduation_progress": _degree_progress(program, courses, transcript, planned),
    }
