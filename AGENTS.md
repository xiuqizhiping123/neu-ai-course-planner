# Repository Guidelines

## General Conventions

- Follow the encoding, line-ending, and indentation settings defined in `.editorconfig`.
- This project is a purely local, single-machine application. Do not consider public network security or data encryption; local files and inter-process communication are trusted by default. Prioritize simplicity and debuggability.

## Dependencies & Verification

- Manage Python dependencies with `uv`: add dependencies using `uv add`, and run tests using `uv run pytest`.
- Place unit tests in `tests/unit/`. Any calls involving models or external APIs must be mocked.
- Place real-model integration tests in `tests/integration/` and mark them with `@pytest.mark.integration`. Do not run them unless explicitly requested.
- After modifying code, prioritize reusing existing tests, type checks, or static analysis for verification. Add or modify tests only when changes introduce new observable behavior or new long-term regression risks.
- Do not add tests for internal changes that do not alter external behavior (such as renaming, moving code, extracting functions, formatting adjustments, or refactoring private implementations), nor mechanically create tests solely because a new function, class, or method was added.
- Use Pyright for all Python type checking. After modifying Python code, run `uv run pyright` and resolve any type errors introduced by your changes.
- Follow the formatting style of surrounding code when editing Python files. After making edits, run `uv run ruff check <file_path>` on the modified Python files. Run `ruff format` only when explicitly requested.

## Decision-Making & User Confirmation

Before implementation, inspect existing implementations, configurations, and documentation in the repository to determine the solution from available context whenever possible.
You must consult the user before proceeding in the following situations:
- The change alters observable results or behavior for callers, users, or downstream systems if they do not adjust their own usage.
- The change affects public interfaces, persistence schemas, message formats, or configuration formats (purely internal refactoring is excluded).
- A new architectural dependency needs to be introduced.
- A critical decision cannot be reliably inferred from requirements or existing repository implementations and will substantially impact the implementation plan.

When the runtime environment provides a structured questioning tool, prefer using it to consult the user.
When consulting the user, present concrete options, key trade-offs, and a recommended default.
Determine all other routine implementation details independently without repeated confirmation. If a task involves any of the decision boundaries above, complete all independent analysis first, obtain confirmation, and then implement the relevant changes.

## Communication Style

- When the user asks a question, answer the question first before editing files or executing implementation instructions.
