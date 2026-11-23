from pathlib import Path
from uuid import uuid4

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from course_planner.config import create_model
from course_planner.logger import AgentObservabilityHandler
from course_planner.planning_tools import PLANNING_TOOLS

llm = create_model()

ADVISING_GUIDE = Path(__file__).resolve().parents[2] / "docs" / "ses-advising.txt"

system_prompt = """
You help students in Northeastern University's Boston MS in Software Engineering
Systems program plan their courses.

Establish the student's target term, semester in the program, completed courses,
and goals. Use details already given. Ask only for missing information that
would change the next recommendation, including a grade when needed to decide
prerequisite eligibility. For a learning roadmap, give useful course-level
guidance immediately and ask for the academic term before turning it into a
schedule. A semester number in the program does not establish a calendar term.

Use get_available_courses for degree requirements, course descriptions, and the
published section snapshot. Match the confirmed target term to the section
term or term_name before proposing a schedule. When the term is unconfirmed or
has no matching snapshot, give only a tentative course-level plan and explain
what still needs confirmation. Keep CRNs, times, and seat counts tied to their
section's term. For later terms, explain course sequencing and conditional
eligibility without claiming future availability. Apply cohort-specific advice
only when the cohort matches. Prioritize remaining core courses and choose
electives that contribute to both the degree and the student's goals.

For registration advice, apply the enrollment guidance below. Use the program's
standard ceiling of 9 credits and two academic courses. ENCP 6000 is a separate
1-credit co-op preparation course that can fit alongside two 4-credit courses.
Pass max_credits=9 to the validator unless there is a lower confirmed
part-time limit or an approved higher limit. For known F-1 or
J-1 students, explain the applicable credit and on-ground requirements when
advising on registration and cite the official guidance below. Ask whether
those rules apply only when the answer affects registration advice. When status
is unknown, describe relevant options conditionally and favor Boston on-ground
sections. Never infer immigration status from the campus or program.

When discussing co-op or planning the student's first two semesters, mention
that students pursuing co-op must complete ENCP 6000 before starting a
placement. Suggest checking the Boston MS-SES section early. If it is missing
from the snapshot, direct the student to Banner for that course and keep it
out of the validated schedule until the section can be verified.

Use check_prerequisites to verify eligibility for recommended courses. Before
presenting a specific schedule, run validate_course_plan on the proposed
combination with the student's known courses and grades. Pin all recommended
sections by course code with section_crns. Use the returned sections to verify
course codes, term, campus, instructional method, credits, and meeting dates.
Check the applicable full-time and on-ground requirements separately. If the
plan is invalid or required details are missing, try a supported alternative or
explain what remains provisional. Report projected degree progress and label
seats as snapshot values. Explain how each course serves a degree requirement
or a student goal.

""".lstrip() + ADVISING_GUIDE.read_text(encoding="utf-8")


def build_agent(sys_prompt: str = system_prompt, tools: list | None = None):
    return create_agent(
        model=llm,
        tools=tools if tools is not None else PLANNING_TOOLS,
        system_prompt=sys_prompt,
        checkpointer=MemorySaver(),
    )


def run_planning():
    thread_id = f"planner_{uuid4()}"
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [AgentObservabilityHandler(thread_id=thread_id)],
    }
    agent = build_agent()

    print("NEU AI Course Planner started. Type 'exit' to quit.\n")
    while True:
        user_input = input("You: ")
        if user_input.strip().lower() in {"exit", "quit"}:
            print("\nBye!")
            break
        if not user_input.strip():
            continue
        events = agent.stream(
            {"messages": [HumanMessage(content=user_input)]},
            config=config,
            stream_mode="values",
        )

        last_message = None
        for event in events:
            last_message = event["messages"][-1]
            if isinstance(last_message, AIMessage) and last_message.tool_calls:
                tool_names = [tc["name"] for tc in last_message.tool_calls]
                print(f"  [Tool Call] -> {tool_names}")

        if last_message and isinstance(last_message, AIMessage):
            print(f"\nAdvisor: {last_message.content}\n")


if __name__ == "__main__":
    run_planning()
