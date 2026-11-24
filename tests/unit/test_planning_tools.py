from typing import Any

import pytest

from course_planner import planning_tools


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    def rule(course: str, grade: str | None = None) -> dict[str, Any]:
        return {"course": course, "min_grade": grade, "allow_concurrent": False}

    def course(
        code: str, credits: float,
        prerequisite: dict[str, Any] | None = None,
        corequisite: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "course_code": code, "credits_min": credits, "credits_max": credits,
            "prerequisites": {
                "status": "parsed" if prerequisite else "none", "expression": prerequisite,
            },
            "corequisites": {
                "status": "parsed" if corequisite else "none", "expression": corequisite,
            },
        }

    def section(
        code: str, crn: str, campus: str, start: str, end: str
    ) -> dict[str, Any]:
        return {
            "course_code": code, "crn": crn, "campus": campus, "credits": None,
            "credits_min": None, "credits_max": None, "seats_available": 1,
            "meetings": [{
                "days": ["monday"], "start_time": start, "end_time": end,
                "start_date": "2027-01-13", "end_date": "2027-05-02",
            }],
        }

    data = {
        "courses": [
            course("INFO 5100", 4, corequisite=rule("INFO 5101")),
            course("INFO 5101", 0, corequisite=rule("INFO 5100")),
            course("CSYE 6200", 4),
            course("CSYE 6225", 4, {
                "op": "OR", "requirements": [
                    rule("INFO 5100", "B-"), rule("CSYE 6200", "B-")
                ]
            }),
        ],
        "sections": [
            section("INFO 5100", "10001", "Boston", "10:00", "11:00"),
            section("INFO 5101", "10002", "Online", "10:00", "11:00"),
            section("INFO 5101", "10003", "Online", "11:00", "12:00"),
            section("INFO 5101", "10004", "Seattle, WA", "12:00", "13:00"),
            section("CSYE 6200", "10005", "Boston", "13:00", "14:00"),
        ],
        "programs": [{
            "program_id": "ms-ses-boston", "campus": "Boston",
            "total_required_credits": 32, "minimum_core_grade": "C", "minimum_gpa": 3,
            "required_courses": [{"course_code": "CSYE 6200", "credits": 4}],
            "electives": {
                "subjects": ["CSYE", "INFO"], "excluded_courses": [],
                "minimum_credits": 28, "minimum_credits_by_subject": {"CSYE": 8},
            },
        }],
    }
    monkeypatch.setattr(planning_tools, "load_runtime_data", lambda: data)
    return data


def test_prerequisite_or_distinguishes_unknown_grade_from_failure(catalog: dict[str, Any]):
    check = planning_tools.check_prerequisites.invoke
    request = {"completed_courses": ["info 5100"], "target_course": "CSYE 6225"}
    assert check(request)["eligible"] is None
    assert check({**request, "grades": {"INFO 5100": "C+"}})["eligible"] is False
    result = check({
        **request,
        "completed_courses": ["INFO 5100", "CSYE 6200"],
        "grades": {"INFO 5100": "B-"},
    })
    assert result["eligible"] is True
    assert result["satisfied"] == ["INFO 5100"]
    assert result["missing"] == []


def test_plan_finds_online_corequisite_without_conflicts(catalog: dict[str, Any]):
    courses = planning_tools.get_available_courses.invoke({})
    assert [row["crn"] for row in courses["sections"]] == ["10001", "10002", "10003", "10005"]
    assert [row["credits_min"] for row in courses["sections"]] == [4, 0, 0, 4]
    request = {
        "student_profile": {"max_credits": 8},
        "proposed_courses": ["INFO 5100", "INFO 5101", "CSYE 6200"],
    }
    validate = planning_tools.validate_course_plan.invoke
    result = validate(request)
    assert result["valid"]
    assert [row["crn"] for row in result["sections"]] == ["10001", "10003", "10005"]
    assert result["credits_max"] == 8
    assert result["graduation_progress"]["remaining_total_credits"] == 24
    assert not validate({**request, "student_profile": {"max_credits": 7}})["valid"]
    assert not validate({**request, "section_crns": {"INFO 5101": "10002"}})["valid"]
    assert not validate({**request, "proposed_courses": ["INFO 5100"]})["valid"]
    assert not validate({**request, "proposed_courses": ["CSYE 6200", "csye 6200"]})[
        "valid"
    ]


def test_tool_argument_validation(catalog: dict[str, Any]):
    check = planning_tools.check_prerequisites.invoke
    validate = planning_tools.validate_course_plan.invoke

    with pytest.raises(ValueError, match="target_course"):
        check({"completed_courses": [], "target_course": "   "})

    with pytest.raises(ValueError, match="Invalid grade"):
        check({
            "completed_courses": ["INFO 5100"],
            "target_course": "CSYE 6225",
            "grades": {"INFO 5100": "85"},
        })

    with pytest.raises(ValueError, match="proposed_courses"):
        validate({"student_profile": {}, "proposed_courses": []})

    with pytest.raises(ValueError):
        validate({"student_profile": {"max_credits": True}, "proposed_courses": ["CSYE 6200"]})

    with pytest.raises(ValueError):
        validate({
            "student_profile": {"unknown_field": 1},
            "proposed_courses": ["CSYE 6200"],
        })
