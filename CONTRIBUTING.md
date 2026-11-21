# Contributing

1. `uv sync` creates the environment.
2. Make the change with a test that fails without it.
3. Run `uv run ruff format src tests examples`, `uv run ruff check src tests examples`, `uv run mypy` and `uv run pytest`.
4. Add a line to `CHANGELOG.md` for user-facing changes.

Ground rules:

- The core (`_faults`, `_plans`, `_scenario`, `_points`, `_report`) must stay free of third-party imports.
- New faults must say exactly what they simulate. If something is not a real network condition, the docs must say so.
- No AI or network-dependent behaviour decides a test outcome.
