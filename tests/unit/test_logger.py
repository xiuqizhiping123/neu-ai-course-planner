import json
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from course_planner import logger as log_module


def test_logs_tool_calls_in_mixed_chat_history(monkeypatch: pytest.MonkeyPatch):
    log = Mock()
    monkeypatch.setattr(log_module, "logger", log)
    handler = log_module.AgentObservabilityHandler("thread")
    ai_message = AIMessage(
        content="",
        tool_calls=[{"name": "check_prerequisites", "args": {}, "id": "call"}],
        usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
    )
    handler.on_chat_model_start({}, [[
        SystemMessage(content="system"),
        HumanMessage(content="plan"),
        ai_message,
        ToolMessage(content="eligible", tool_call_id="call"),
    ]])
    history = json.loads(log.info.call_args.args[2])
    assert history[0]["content"] == "<SystemPrompt len=6>"
    assert history[2]["tool_calls"] == ai_message.tool_calls
    assert "tool_calls" not in history[1]
    assert "tool_calls" not in history[3]

    handler.on_llm_end(LLMResult(generations=[[], [ChatGeneration(message=ai_message)]]))
    response = json.loads(log.info.call_args.args[2])
    assert response["tool_calls"] == ai_message.tool_calls
    assert response["token_usage"] == ai_message.usage_metadata


def test_logs_empty_model_response_without_index_error(monkeypatch: pytest.MonkeyPatch):
    log = Mock()
    monkeypatch.setattr(log_module, "logger", log)
    handler = log_module.AgentObservabilityHandler("thread")
    handler.on_llm_end(LLMResult(
        generations=[[]], llm_output={"token_usage": {"total_tokens": 3}},
    ))

    assert json.loads(log.info.call_args.args[2]) == {
        "duration_sec": 0.0,
        "token_usage": {"total_tokens": 3},
        "content": "",
        "tool_calls": [],
    }
