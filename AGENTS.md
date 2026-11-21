# Project notes

START Hack Munich 2026, Lio challenge. Submission deadline: Sunday 22 Nov 2026, 12:00 Europe/Berlin.

## Rules
- Portal data (`data/`), the challenge PDFs and `.env` must never be committed. The data may not leave the team.
- No supplier email is sent without explicit approval from the team.
- Findings must be derived from the files at runtime. Never copy values from the data into code.

## Commands (run from `backend/`)
- Tests: `uv run pytest -q`
- Sync the portal mirror once: `uv run python -m app.portal` (writes `data/portal`, `data/blobs`, `data/state.sqlite`)
- New dependencies: `uv add <pkg>`; `[tool.uv] exclude-newer` in `pyproject.toml` keeps out releases younger than a week.

## Git
- No global git identity on this machine. Commit with
  `git -c user.name="Hari0m-git" -c user.email="harisankarkaramchand@gmail.com" commit ...`
- Push with `git -c credential.helper= -c credential.helper='!gh auth git-credential' push`

## Deploy (Render free web service, root directory `backend`)
- Build: `pip install -r requirements.txt`. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- After changing dependencies, regenerate: `uv export --no-dev --no-hashes --no-emit-project -o requirements.txt`
- Free tier limits that shape the design: the disk is wiped on every restart, the service stops after
  15 idle minutes (hence the self-ping), and outbound SMTP ports 25/465/587 are blocked.

## Reading files (run from `backend/`)
- `uv run python -m app.ingest` reads file versions not read yet; `--all` reads everything again.
- CSVs are mapped onto the vocabulary in `app/entities.py` by a model, then validated and applied by code
  (`app/tabular.py`). Check `ingest_log` for files marked `needs_review`.
- Mailbox sign-in (once, opens a browser): `uv run python -m app.gmail_auth`

## Tooling gotcha
- Shell heredocs in the agent tool collapse double backslashes and cut off after roughly 10k characters.
  Write larger files, and anything containing backslash escapes, with the file tool.

## Running the whole thing
- Backend with the loop (watch, read, reconcile, decide, listen): `uv run uvicorn app.main:app --port 8000` from `backend/`.
- Frontend: `npm run dev` in `frontend/` (proxies `/api` to port 8000). `npm run build` writes the bundle to
  `backend/app/static`, which the backend serves and which is committed, because the host builds Python only.
- The deployed service needs `APP_ACCESS_KEY`; without it every data endpoint answers 503 on purpose.
- Nothing is ever sent except through `POST /api/messages/{id}/send`, i.e. a human clicking approve.
