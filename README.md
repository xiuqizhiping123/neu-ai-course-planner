# NEU AI Course Planner (Mini Project)

An LLM-powered academic advising agent built to help Northeastern University students generate feasible, personalized
semester course plans with deterministic prerequisite validation and explainable requirement justifications.

## Quick Start With Sample Data

Run these commands from the root of a fresh checkout with `uv` installed. The project requires Python 3.14 or newer.

1. Install the locked dependencies and copy the bundled Spring 2027 release into the Agent's runtime directory.

   ```text
   uv sync --locked
   uv run python -c "from shutil import copytree; copytree('sample_data/runtime', 'data/runtime')"
   ```

   If you have already copied the sample release, skip the copy step. The bundled data covers the Boston
   MS-SES program and requires no data collection or cleanup before the demo.

2. Create a `.env` file in the repository root and supply your own API key. The default model is DeepSeek Chat.

   ```dotenv
   OPENAI_API_KEY=your_deepseek_api_key
   ```

   For another OpenAI-compatible provider, set `OPENAI_BASE_URL` and `PLANNER_MODEL` to match that provider.
   The model call requires internet access. Course data and validation run locally.

3. Start the Agent, then paste a prompt from [Evaluation & Demo Scenarios](#3-evaluation--demo-scenarios).
   Type `exit` to quit.

   ```text
   uv run python -m course_planner.agent
   ```

---

## 1. Project Scope & Architecture Strategy

Target Program: Built for Northeastern University’s MS in Software Engineering Systems.

Course Data: The bundled Spring 2027 release contains 33 course records and 33 Boston sections, with credits,
prerequisites, and meeting details.

Explainable Schedules: Every recommendation explains how it meets prerequisites, credit limits, and graduation rules.

---

## 2. Core Agent Tools

Instead of fragile web scrapers, the agent relies on three deterministic local Python tools to execute a
**Plan $\rightarrow$ Verify $\rightarrow$ Self-Correct** loop:

The Python functions live in `course_planner.planning_tools`.

1. **`get_available_courses(program_id="ms-ses-boston")`** returns the program rules, course metadata, and
   sections in the active release. A course without a listed section can still appear as a core course or dependency.
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

Start a fresh CLI session for each scenario. The scheduling examples use the bundled Spring 2027 snapshot.

### Scenario 1 — First-semester course plan

> I'm a new Boston MS-SES student starting in Spring 2027. I haven't taken any courses yet. I want to learn Java and
> backend databases. Can you schedule CSYE 6200 and DAMG 6210 for a total of 8 credits?

**Pass when** the assistant calls `validate_course_plan` before presenting a conflict-free 8-credit schedule with
Boston sections for both courses.

### Scenario 2 — Missing prerequisite grade

> I'm in my second semester and have completed INFO 5100. Do I meet the prerequisite for CSYE 6225?

When the assistant asks for the INFO 5100 grade, reply `B-`.

**Pass when** the assistant uses `check_prerequisites` to identify the missing grade, asks for it, then rechecks and
confirms prerequisite eligibility after receiving `B-`.

### Scenario 3 — Revise an overloaded schedule

> I'm planning my second semester in Boston MS-SES for Spring 2027. I've completed CSYE 6200 (A-) and INFO 6205 (B),
> and I can take up to 8 credits. I'm interested in AI and data engineering. Can I take INFO 6105, CSYE 7380, and
> DAMG 6210 together? If that's too many credits, pick two of those courses, verify the revised schedule, and tell me
> which core requirements and credits I'd still need.

**Pass when** the assistant:

- Calls `validate_course_plan` on all three courses and explains why 12 credits exceed the 8-credit limit.
- Calls the validator again with the revised pair and its CRNs before recommending it. One valid pair is
  `INFO 6105` + `CSYE 7380`, which supports the student's AI and data engineering interests.
- Identifies `CSYE 7230` as the remaining core course and distinguishes the 24 credits still needed today from
  the 16 credits projected to remain after completing the revised plan.

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
