import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def setup_logger(name: str = "course_planner",
                 level: int = logging.INFO) -> logging.Logger:
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
        self.llm_start_time = 0.0

    def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any
    ) -> None:
        self.llm_start_time = time.perf_counter()

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        elapsed = time.perf_counter() - self.llm_start_time
        token_usage = (
            response.llm_output.get("token_usage", {})
            if response.llm_output
            else {}
        )
        logger.info(
            "[%s] LLM call completed | Duration: %.2fs | Token usage: %s",
            self.thread_id, elapsed, token_usage,
        )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        logger.error("[%s] LLM call failed: %s", self.thread_id, error, exc_info=error)

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        logger.error("[%s] Tool execution failed: %s", self.thread_id, error,
                     exc_info=error)
