# Contributing

## Development Setup

1. Create a virtual environment.
2. Install dependencies with `pip install -e .[dev]`.
3. Copy `.env.example` to `.env` and set local `API_TOKEN` and `ADMIN_TOKEN`.
4. Run tests with `pytest -q`.
5. For simulator verification, run `.\scripts\run_and_watch.ps1`.

## Pull Request Expectations

- Keep changes scoped and well described.
- Add or update tests for behavior changes.
- Do not include secrets, tokens, or local data dumps.
- Keep API/WebSocket envelope fields backward compatible unless `contract_version` is bumped.
- For beta, prioritize correctness of signal/adaptation behavior; note any intentional security tradeoffs briefly in the PR if relevant.

## Code Standards

- Prefer explicit typing.
- Validate all external inputs.
- Keep business logic out of route handlers.
