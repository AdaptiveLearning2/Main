# Contributing

## Development Setup

All from `C:\AdaptiveLearning\EEGResearch` unless a step says otherwise. `docs/DEV_QUICKSTART.md`
has the same steps with the verification commands; the reasons behind each rule are in the
repo-root `CLAUDE.md` under *Running and testing*.

1. Create the venv here: `python -m venv .venv` and activate it.
2. Install **from this directory**: `pip install -e ".[dev]"`, or `pip install -e ".[dev,face,gaze]"`
   if you will touch the camera or gaze channels. Run anywhere else, pip resolves `.` to the repo
   root and reports "neither setup.py nor pyproject.toml found". `face` pins
   `opencv-contrib-python`; never install `opencv-python` beside it.
3. Copy `.env.example` to `.env` and set real `API_TOKEN` and `ADMIN_TOKEN`; the app refuses to
   start without them.
4. Run the tests **from the repo root, with this venv's interpreter and the env set**:

   ```powershell
   cd C:\AdaptiveLearning
   $env:EEG_SOURCE = "sim"; $env:API_TOKEN = "t"; $env:ADMIN_TOKEN = "a"
   EEGResearch\.venv\Scripts\python.exe -m pytest EEGResearch\tests -q
   ```

   Not bare `pytest -q`, and not from this directory: bare `pytest` takes whatever interpreter is
   on `PATH`, and `Settings` loads `.env` relative to the cwd, so the `.env` you edited in step 3
   overrides field defaults for every test that constructs `Settings()` and produces `test_face_*`
   failures that read as a code regression.
5. For simulator verification, `.\scripts\run_and_watch.ps1` starts the API on **8001** (the
   sidecar's port; 8000 is the website backend's) and tails live state. Add a test for any
   behaviour change and check it by putting the bug back: a test that passes against the bug it
   was written for is worse than none.

## Pull Request Expectations

- Keep changes scoped and well described.
- Add or update tests for behavior changes, and mutation-check each new test.
- Do not include secrets, tokens, or local data dumps. Recordings of a person never enter the
  repo; they live outside it (`C:\eeg_captures\`).
- Keep API/WebSocket envelope fields backward compatible unless `contract_version` is bumped. A
  new key on the payload has to be declared on the pydantic model (`schemas.FeatureData` for a
  feature) or `/api/v1/state` drops it silently.
- Keep `CLAUDE.md` current in the same change that makes it true.

## Code Standards

- Prefer explicit typing.
- Validate all external inputs; anything that arrives in `raw` on the push path is client-supplied.
- Keep business logic out of route handlers.
