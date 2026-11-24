from collections import Counter
from typing import Any

from langchain.agents.middleware import AgentState, after_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime


@after_model(can_jump_to=["model", "end"])
def recover_tool_calls(state: AgentState, _runtime: Runtime) -> dict[str, Any] | None:
    """Allow one correction round per failing tool in the current user turn."""
    latest = state["messages"][-1]
    if not isinstance(latest, AIMessage) or not (
        latest.tool_calls or latest.invalid_tool_calls
    ):
        return None

    failures: Counter[str] = Counter()
    failed_tools: set[str] = set()
    for message in reversed(state["messages"][:-1]):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, ToolMessage) and message.status == "error" and message.name:
            failed_tools.add(message.name)
        elif isinstance(message, AIMessage):
            failures.update(failed_tools)
            failed_tools.clear()

    errors: list[ToolMessage] = []
    pending = False
    can_correct = False
    for call in [*latest.tool_calls, *latest.invalid_tool_calls]:
        name, call_id = call.get("name"), call.get("id")
        if not name or not call_id:
            continue
        if failures[name] >= 2:
            detail = "The tool retry has already failed for this request."
        elif call.get("type") == "invalid_tool_call":
            detail = "Tool arguments were malformed. Retry with a valid JSON object."
            can_correct = True
        else:
            pending = True
            continue
        errors.append(ToolMessage(
            content=detail, name=name, tool_call_id=call_id, status="error",
        ))

    if not errors:
        return None
    if pending:
        return {"messages": errors}
    if can_correct:
        return {"messages": errors, "jump_to": "model"}
    return {
        "messages": [*errors, AIMessage(content=(
            "I couldn't complete the tool check after retrying. "
            "The result is still unverified."
        ))],
        "jump_to": "end",
    }
