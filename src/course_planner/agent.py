from uuid import uuid4

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from course_planner.config import create_model
from course_planner.planning_tools import PLANNING_TOOLS

llm = create_model()

system_prompt = """
You are an academic advisor for Northeastern University's MS in Software Engineering Systems program.
Always verify prerequisites and validate the full semester plan using your tools before recommending courses.
Explain clearly why every recommended course satisfies academic constraints and student goals.
"""


def build_agent(sys_prompt: str = system_prompt, tools: list | None = None):
    return create_agent(
        model=llm,
        tools=tools if tools is not None else PLANNING_TOOLS,
        system_prompt=sys_prompt,
        checkpointer=MemorySaver(),
    )


def run_planning():
    config: RunnableConfig = {"configurable": {"thread_id": f"planner_{uuid4()}"}}
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
