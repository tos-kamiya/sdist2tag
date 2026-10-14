# Repository Guidelines

## Project Structure & Module Organization
- `src/sdist2tag/`: Installable package and command-line implementation.
  `cli.py` defines command parsing and reports; `core.py` handles PyPI access,
  archive validation, Git history comparison, and local tag creation.
- `tests/`: Tests named `test_*.py`.
- Root: `pyproject.toml`, `README.md`, and dated handoff/specification files.
- Keep this developer tool independent of zstarview runtime code and GUI
  dependencies.

## Documentation Roles
- `README.md`: Installation, CLI usage, supported behavior, and limitations.
- `handoff-*.md`: Detailed project requirements and rationale. Keep it aligned
  with implemented behavior when the scope or comparison rules change.
- `AGENTS.md`: Contributor workflow and safety rules for this repository.

## Build, Test, and Development Commands
- Use CPython 3.10 or newer. Create an environment with `uv venv --python 3.10`
  or a newer CPython, then install the project with `uv pip install -p
  .venv/bin/python -e .`.
- Run the CLI with `uv run -p .venv/bin/python sdist2tag --help`.
- Run tests with `uv run -p .venv/bin/python python -m unittest discover -s
  tests -v` or `uv run -p .venv/bin/python pytest` when pytest is installed.
- Check syntax with `uv run -p .venv/bin/python python -m compileall -q src
  tests`.
- Build distributions with `uv run -p .venv/bin/python -m build` when the
  `build` package is installed.

## Coding Style and Naming Conventions
- Support CPython 3.10+. Use 4-space indentation, `snake_case` for modules,
  functions, and variables, `CamelCase` for classes, and `UPPER_SNAKE_CASE` for
  constants. Add type hints to public or non-trivial functions.
- Prefer the Python standard library; keep runtime dependencies empty unless a
  concrete need justifies adding one.
- CLI help, terminal output, logs, and exception messages must be ASCII-only.
  JSON reports may contain Unicode data and should serialize it safely.
- Keep archive comparison byte exact. Do not normalize source text, widen the
  generated-file exclusion list without documenting the reason, or infer a
  match from timestamps or version metadata.

## Testing Guidelines
- Cover archive validation, PyPI digest handling, first-parent ordering,
  mismatch reporting, shallow-history handling, and tag conflict safeguards.
- Use temporary Git repositories and small synthetic archives for tests. Tests
  must not access the network, mutate a real repository, or create real project
  tags.
- Run the relevant test suite after changing comparison, archive, or tagging
  behavior.

## Safety and Scope
- Normal inspection is read-only: do not check out files, change refs, fetch,
  push, publish, or delete PyPI files.
- Create local annotated tags only when the user explicitly requests that
  operation or invokes the CLI's `tag` subcommand. Check all target tag names
  before creating any tag. An existing tag at the detected commit is already
  correct; a same-name tag at another commit is an error. Never move or delete
  an existing tag.
- Never run package build hooks or extract archive members to the filesystem.
  Reject unsafe paths and treat links or other special tar members as requiring
  investigation.
- A match means the sdist's included files match a commit. It does not prove
  that commit was used to build the release, and files absent from the sdist
  are outside the comparison.

## Commit and Pull Request Guidelines
- Use Conventional Commit subjects such as `feat:`, `fix:`, `docs:`, and
  `test:`. Keep each commit focused and describe user-visible CLI changes and
  validation steps in pull requests.
- Run Git commit operations with elevated permissions.
- When bumping the project version, update `release-notes.md` with the changes
  for that release and include it in the same commit as the version bump.
- When changing behavior, update the README and the relevant handoff document
  so the documented safety and matching rules stay accurate.
