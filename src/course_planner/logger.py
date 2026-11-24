import json
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def setup_logger(
    name: str = "course_planner", level: int = logging.INFO
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s:%(funcName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_DIR / "planner.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()


class AgentObservabilityHandler(BaseCallbackHandler):
    def __init__(self, thread_id: str = "unknown"):
        self.thread_id = thread_id
        self._start_times: dict[str, float] = {}

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        **kwargs: Any,
    ) -> None:
        run_id = str(kwargs.get("run_id", "default"))
        self._start_times[run_id] = time.perf_counter()
        batch = messages[0] if messages else []
        formatted_msgs = []
        for idx, msg in enumerate(batch):
            if idx == 0 and msg.type == "system":
                content_preview = f"<SystemPrompt len={len(str(msg.content))}>"
            else:
                content_preview = msg.content

            entry: dict[str, Any] = {"role": msg.type, "content": content_preview}
            if isinstance(msg, AIMessage) and msg.tool_calls:
                entry["tool_calls"] = msg.tool_calls
            formatted_msgs.append(entry)

        logger.info(
            "[%s] LLM Request | Messages:\n%s",
            self.thread_id,
            json.dumps(formatted_msgs, ensure_ascii=False, indent=2, default=str),
        )

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id", "default"))
        start_time = self._start_times.pop(run_id, None)
        elapsed = (
            time.perf_counter() - start_time if start_time is not None else 0.0
        )

        generation = next(
            (item for batch in response.generations for item in batch), None
        )
        ai_message = None
        if isinstance(generation, ChatGeneration) and isinstance(
            generation.message, AIMessage
        ):
            ai_message = generation.message

        token_usage = (
            (ai_message.usage_metadata if ai_message is not None else None)
            or (response.llm_output.get("token_usage") if response.llm_output else None)
            or {}
        )

        payload = {
            "duration_sec": round(elapsed, 2),
            "token_usage": token_usage,
            "content": generation.text if generation is not None else "",
            "tool_calls": ai_message.tool_calls if ai_message is not None else [],
        }

        logger.info(
            "[%s] LLM Response:\n%s",
            self.thread_id,
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id", "default"))
        self._start_times.pop(run_id, None)
        logger.error("[%s] LLM call failed: %s", self.thread_id, error, exc_info=error)

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        logger.info("[%s] Tool Output: %s", self.thread_id, str(output)[:1000])

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        logger.error(
            "[%s] Tool execution failed: %s", self.thread_id, error, exc_info=error
        )
