# NEU AI Course Planner (Mini Project)

An LLM-powered academic advising agent built to help Northeastern University students generate feasible, personalized
semester course plans with deterministic prerequisite validation and explainable requirement justifications.

---

## 1. Project Scope & Architecture Strategy

Target Program: Built for Northeastern University’s MS in Software Engineering Systems.

Course Data: Uses a local JSON catalog of ~15–20 courses (INFO, CSYE, ENCP) with credits, tags, and prerequisites.

Explainable Schedules: Every recommendation explains how it meets prerequisites, credit limits, and graduation rules.

---

## 2. Core Agent Tools

Instead of fragile web scrapers, the agent relies on three deterministic local Python tools to execute a
**Plan $\rightarrow$ Verify $\rightarrow$ Self-Correct** loop:

The Python functions live in `course_planner.planning_tools`.

1. **`get_available_courses(program_id="ms-ses-boston")`** returns the program rules, course metadata, and published
   Boston and Online sections. A course without a listed section can still appear as a core course or dependency.
2. **`check_prerequisites(completed_courses, target_course, grades=None, planned_courses=None)`** checks prerequisites
   and corequisites. Pass known letter grades as a mapping from course codes to grades. An `eligible` value of
   `None` means the available information is insufficient to decide.
3. **`validate_course_plan(student_profile, proposed_courses, section_crns=None)`** checks one term for applicable
   prerequisites, corequisites, duplicates, known time conflicts and a supplied credit limit. The student profile
   accepts `completed_courses`, `grades`, `max_credits`, and `program_id`. Pass a course-to-CRN mapping to pin
   sections. The result includes selected sections, a credit range, and projected coursework progress. Check
   `schedule_complete` for missing meeting times. Seat counts reflect the snapshot.

---

## 3. Evaluation & Demo Scenarios

Use these scenarios to exercise the planning tools:

* **Scenario 1: First-Semester Onboarding (Happy Path)**
* *Profile:* Incoming student with `[]` completed courses, 8-credit limit, interested in `["Backend", "Java"]`.
* *Expected Behavior:* Validates `INFO 5100`, its zero-credit `INFO 5101` Online lab, and `CSYE 6200` together against
  the 8-credit cap and class times.


* **Scenario 2: Prerequisite Conflict & Self-Correction**
* *Profile:* Second-semester student who completed `INFO 5100` and requests Cloud Computing (`CSYE 6225`).
* *Expected Behavior:* `check_prerequisites` asks for the missing grade through its result. A grade of `B-` or higher in
  either `INFO 5100` or `CSYE 6200` satisfies the requirement.


* **Scenario 3: Credit Overload & Graduation Requirement Guardrail**
* *Profile:* Graduating student with an 8-credit cap proposes 12 elective credits and has yet to take `CSYE 7230`.
* *Expected Behavior:* `validate_course_plan` reports the overload and lists `CSYE 7230` in the remaining core requirements.

## 4. Course Data Pipeline

Run from the repository root and replace the example snapshot names with the actual directory names.

1. **Refresh and clean.** Merge a partial refresh with a complete snapshot from the same term.

   ```powershell
   uv run python scripts/fetch_neu_data.py --source banner
   uv run python -m scripts.data_pipeline prepare --base data/raw/FULL_SNAPSHOT --refresh data/raw/PARTIAL_SNAPSHOT --output-dir data/cleaned/DRAFT
   ```

2. **Review.** Read the draft's `review.json` and copy `corrections_template.json` to `data/corrections.json`.
   Record a reason for each correction and cite evidence for historical course codes.
   Read `program_requirements.md` for the catalog text. The program JSON records the constraints
   needed for validation, including 20 elective credits with at least 8 from CSYE.
   Rebuild earlier drafts with `prepare` to generate the document and updated constraints.

3. **Publish.** Rebuild older drafts to include Online sections available to Boston students.
   Publication validates Boston and Online sections, MS-SES core courses, and their dependencies.

   ```powershell
   uv run python -m scripts.data_pipeline publish data/cleaned/DRAFT --corrections data/corrections.json
   ```

   The program reads the current release with `course_planner.catalog_data.load_runtime_data()`.
   A failed publication leaves the previous release available.
