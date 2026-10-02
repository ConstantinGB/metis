# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

METIS catalogues a codebase (files, symbols, connections) and shows it as a clickable column browser
for novice programmers. `CONTEXT.md` is the pitch, `PLAN.md` the agreed design and decision log.
Update PLAN.md when a design decision changes.

## Commands

```
uv sync                                  # install (Python 3.12, deps + dev group)
uv run pytest -q                         # full suite (~3s, no network, no AI)
uv run pytest tests/test_scan.py::test_go -q      # one test
uv run ruff check src tests && uv run ruff format src tests
uv run metis serve                       # web GUI on http://127.0.0.1:8765, opens the browser
METIS_HOME=/tmp/m uv run metis create demo && METIS_HOME=/tmp/m uv run metis add demo tests/fixtures/pyproj
uv run metis scan demo [--repo R] [--branch B]     # headless Local Scan
uv run metis ai-scan demo R [--force]              # headless AI Scan (real Claude call, costs money)
uv run metis explain-diff demo R base head
```

`METIS_HOME` overrides the data directory (default `~/.metis`). Always set it for manual experiments so
the user's real projects are untouched. Tests set it to a tmp dir via the `metis_home` fixture.

The AI Scan needs the `claude` CLI on PATH and a login (`claude auth status`). Nothing in the test
suite calls Claude; the AI tools are tested by calling `MetisTools` methods directly.

## Architecture

Layers, each depending only on the ones below it (`src/metis/`):

| Layer | Modules | Role |
|---|---|---|
| web | `web/app.py`, `web/jobs.py`, `web/tree.py`, templates, static | FastAPI routes, background jobs, column browser |
| render | `render/markdown.py`, `render/source.py` | one node -> `Card` (Markdown) -> HTML; Pygments source view with clickable names |
| store | `store.py` | the only read path for the UI: cached scans, cross-repo edge index, AI JSON files |
| ai | `ai/runner.py`, `ai/templates.py`, `ai/packets.py`, `ai/tools.py`, `ai/prompts.py`, `ai/stale.py`, `ai/costlog.py`, `ai/search.py`, `ai/skills/` | AI Scan in three stages (templates: no model; packets: one no-tool call per file on the packet model; agent: unclear files + overview), stale marking, cost log, two-stage AI search |
| scan | `scan/runner.py`, `walker.py`, `parsers.py`, `symbols.py`, `imports/`, `linker.py`, `docs.py`, `roles.py`, `frameworks.py`, `sqltext.py`, `gitsignals.py`, `lsp.py` | Local Scan: files -> symbols -> docs/roles/framework facts -> imports -> edges; git signals; optional LSP refinement |
| projects | `projects/registry.py`, `projects/git.py` | project.json, clones, worktrees, git subprocess |
| model | `model/core.py`, `model/ids.py` | Pydantic models = the JSON contract; node id scheme |

Invariants worth keeping:

- **Node ids** (`model/ids.py`): `r:<repo>`, `d:<repo>:<dir>`, `f:<repo>:<path>`, `s:<repo>:<path>#<qualified>`,
  `x:<external>`. Everything (edges, AI output, URLs, Markdown links) uses these strings.
- **Local Scan is deterministic and offline.** Same commit in, identical JSON out (lists are sorted).
  `test_deterministic` enforces it.
- **`tree-sitter` is pinned below 0.26.** 0.26.0 segfaults when Python objects are allocated while
  iterating `QueryCursor.matches` results (reproduced on `src/metis/ai/tools.py`; `tests/test_dogfood.py`
  guards it). Re-test before lifting the pin.
- **Symbols come from tree-sitter tags queries** (`tree-sitter-language-pack`), one generic extractor for all
  languages. Languages without a bundled tags query (bash, sql, yaml) are handled by `scan/imports/*`
  handlers, which may also override symbols. TypeScript needs the JavaScript query prepended
  (`parsers.INHERITS`). Python module-level assignments are extracted in code because the bundled pattern
  does not match under this binding.
- **Import resolution is per language** (`scan/imports/`). Order: same repo -> another repo in the project
  (matched via `Repo.package_names` / `go_module`) -> external. Stdlib imports are recorded but produce no edge.
- **Edges** (`scan/linker.py`): `imports`/`runs`/`includes`/`references` from resolved imports; `calls`/`inherits`
  from tags references: receiver-aware first (`self.x`, module aliases, `var = Class()` tracking), then same
  file, imported files (re-exports followed to any depth), then repo-wide only if unique (`heuristic`).
  Parameters/locals shadowing a name produce no edge. `handles` from route/command symbols
  (`scan/frameworks.py`), `references`/`defines` to `env:<KEY>` nodes, SQL tables parsed from string
  literals (`scan/sqltext.py`, exact), `tests` from test naming conventions. File metrics come from edges.
- **Docs and roles** (`scan/docs.py`, `scan/roles.py`): comment blocks above definitions become
  `Symbol.docstring` for every language; the file header becomes `FileEntry.header_doc` unless it sits
  directly above the first definition. Roles (`entry`, `route`, `test`, `ci`, `generated`, ...) give
  `title_hint`, which cards use before any AI runs and packets use as the draft title.
- **Git signals** live in `git.json` next to `local.json`, never inside it, so the scan stays deterministic.
- **LSP tier** (`scan/lsp.py`) runs only when a server is on PATH and `use_language_servers` is on; it may
  upgrade or add `calls` edges (note `lsp`), never remove any. Results cache in `lsp-cache.json`.
- **AI output is written only through `ai/tools.record_summary`** (agent submit tool and packet runner both
  use it), after Pydantic validation, so a malformed model response never reaches disk. One JSON per file,
  keyed by content hash; `source` says template/packet/agent. `ai/stale.py` marks importers stale when a
  file's symbols or import targets change; stale files are re-run first. Every model call is appended to
  `ai/_log.jsonl`. Skill texts in `ai/skills/*/SKILL.md` are inlined into byte-stable system prompts.
- **Cards link with `node:<url-encoded id>` hrefs.** `static/metis.js` turns those into column-browser
  navigation (Finder semantics: clicking in column N replaces columns > N; Ctrl/middle-click opens a new
  tab). Tabs are rows of cards encoded in the URL (`?tabs=a,b|c&active=1`, ids URL-encoded twice), no
  server state. Pseudo-ids `q:<query>` and `ai:<question>` render search-result cards. `line:N` hrefs
  scroll the card's source view. Source HTML comes from `GET /p/{p}/source?node=` on first expand.
- **Branches coexist.** The main clone stays on the default branch; other branches get `git worktree`s
  under `<project>/worktrees/`. Scans are keyed by `repo/branch`.

On-disk layout (per project, under `METIS_HOME/projects/<name>/`): `project.json`, `repos/<repo>/`,
`worktrees/<repo>/<branch>/`, `scans/<repo>/<branch>/local.json`, `scans/<repo>/<branch>/ai/*.json`,
`scans/<repo>/diffs/`. Settings in `METIS_HOME/settings.json`, GitHub tokens in `secrets.json` (mode 600).

Adding a language: map its extension in `walker.LANGUAGE_BY_EXT`; if the pack has a tags query, symbols
work immediately; add a handler in `scan/imports/` for imports and register it in `scan/imports/__init__.py`.

Test fixtures are tiny real repos in `tests/fixtures/` (pyproj, jsproj, goproj, delta); `fixture_repo`
copies one to tmp and `git init`s it. Add a case there rather than mocking the scanner.
