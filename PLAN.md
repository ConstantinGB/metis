# METIS Plan

Status: v1 and v1.1 built (2026-09-24/25). v1.2 (Part B, phases 13-19) built 2026-10-01. Measured on the
`webproj` fixture: 14 files = 5 templates (free) + 9 packets on claude-haiku-4-5 ($0.14, about $0.016 per
file, thinking off) + agent for the overview only ($0.11); the v1 agent cost about $0.027 per file. The LSP
tier was verified with pyright on the METIS repo (heuristic call edges 261 -> 154 in 25 s). Source of intent: `CONTEXT.md`; day-to-day conventions: `CLAUDE.md`.

Part A describes METIS as it exists today. Part B is the roadmap.

---

# Part A: current state (as built)

## A1. Decisions in force

| Topic | Decision |
|---|---|
| Runtime | Python 3.12, `uv`, single package `metis`; `tree-sitter` pinned below 0.26 (0.26.0 segfaults) |
| GUI | FastAPI + Jinja2 server-rendered pages, ~250 lines of vanilla JS; dark theme default, light in Settings |
| Local Scan | tree-sitter via `tree-sitter-language-pack`; generic tags-query symbol extraction; per-language import handlers |
| AI backend | Claude Agent SDK; needs the Claude Code CLI on PATH and a login. Default model `claude-sonnet-5`, cap 5 USD per run |
| Data | `~/.metis/projects/<name>/` (override with `METIS_HOME`); JSON files with Pydantic models as the schema |
| Git | subprocess `git`; one worktree per opened branch so branches coexist; optional GitHub token in `secrets.json` (mode 600) |
| Presentation | JSON -> Markdown -> HTML cards in a column browser; Pygments source view; tabs encoded in the URL |

## A2. Architecture

```
web/       routes, background jobs, tree, templates, metis.js
render/    markdown.py (cards), source.py (Pygments view with clickable names)
store.py   the only read path for the UI: cached scans, cross-repo edge index, AI JSON
ai/        runner.py (AI Scan agent), tools.py (validating MCP tools), search.py (two-stage AI search), prompts, skills/
scan/      walker, parsers, symbols, imports/{python,javascript,go,yaml_,bash,sql}, linker, runner
projects/  registry (project.json, clones, worktrees), git
model/     core.py (Pydantic models), ids.py (node id scheme)
```

Rules: `scan/` is offline and deterministic; `ai/` only reads scan JSON and writes AI JSON through
validating tools; `render/` and `web/` read through `store.py` and enqueue jobs; every JSON file has
a model and a `schema_version`.

Node ids: `r:<repo>`, `d:<repo>:<dir>`, `f:<repo>:<path>`, `s:<repo>:<path>#<qualified>`, `x:<external>`;
pseudo-ids `q:<query>` and `ai:<question>` for search cards.

## A3. What the Local Scan produces today

- Files with language, size, lines, content hash, doc flag, skip reason.
- Symbols from tags queries (functions, methods, classes, constants, module variables, YAML keys,
  SQL tables/views/procedures, shell functions) with lines, signature, Python docstring, parent.
- Imports resolved in order same repo -> other repo in the project -> external; stdlib flagged.
  Python (relative, packages, one level of `__init__` re-export), JS/TS (relative, index files,
  `@/` alias, package names), Go (module path, packages as directories), YAML (paths and `run:`
  commands), Bash (`source`, invoked repo files), SQL (`REFERENCES`, `SOURCE`).
- Edges: `imports`, `runs`, `includes`, `references` (exact) from imports; `calls` and `inherits`
  from call-site names matched in the same file, then imported files, then repo-wide if unique
  (`heuristic`); SQL table names found in string literals (`heuristic`).

## A4. What the AI does today

- **AI Scan**: one agent session per repo per run (read-only tools plus `metis_*` MCP tools), file by
  file, leaves first. Writes one `AiFileSummary` per file (title, purpose, per-function explanation,
  connections, rejected heuristic targets, notes) and an `AiRepoOverview`. Incremental by content
  hash, capped by budget, up to six continuation rounds.
- **Explain diff**: one short session over a branch diff plus known summaries.
- **AI search**: stage 1 is one no-tool call over a text index of names and summaries with a JSON
  output schema; stage 2 is an optional read-only agent. Results cached per question.

## A5. GUI today

Project page (repos, branches, scans, jobs, costs), column browser with tabs, search card and AI
search card as column 1, source view with highlight toggle and clickable names, diff page with AI
explanation, Settings (theme, model, budget, limits).

---

# Part B: v1.2 roadmap

Two goals, in this order of priority:

1. **Maximum value offline.** Everything a script can establish about structure, flow, and intent
   should be established without AI, so a project with no Claude access is still a useful compendium.
2. **A cheap, fast AI Scan.** The AI should start from the scan's facts and read as little code as
   possible, in parallel, with the agent loop reserved for files the facts cannot explain.

The two reinforce each other: every fact the scan adds is a fact the AI no longer pays to discover.

## B1. Offline analysis: what scripts can add

Ordered by value for the effort. Each item is a self-contained change to `scan/` with a model
addition and a fixture test.

### B1.1 Author documentation for every language

Today only Python docstrings reach the card. Add a `scan/docs.py` extractor for the comment block
that immediately precedes or opens a definition: JSDoc and `//` runs (JS/TS), `//` doc comments
(Go, Rust `///`), Javadoc and KDoc, Ruby `#`, PHP docblocks, Bash `#` headers, SQL `--` headers, and
the file-level header comment or module docstring. Store on `Symbol.docstring` and a new
`FileEntry.header_doc`. Strip markup (`@param`, `*`) to plain text, keep the first paragraph for
cards and the whole text for AI packets.

Payoff: a large share of files and functions get an author-written purpose line with no AI.

### B1.2 File roles

`scan/roles.py` classifies every file into one or more roles from path, name, imports and markers:
`entry` (`if __name__`, `func main`, `main()` export, CLI framework registration), `route`
(FastAPI/Flask/Django/Express/Gin decorators or router calls), `test` (path, framework imports),
`config`, `ci`, `migration`, `schema`, `model` (ORM/dataclass heavy), `script`, `doc`, `build`,
`generated` (header markers, protobuf, minified), `vendored` (`vendor/`, `third_party/`,
`node_modules`). Stored as `FileEntry.roles: list[str]` and `FileEntry.title_hint` ("CI workflow -
GitHub Actions", "Test module - pytest"), the same shape the AI title uses.

Payoff: cards get a title before AI; the AI Scan can skip `generated`/`vendored` and template-summarise
trivial files; the repo card can list entry points from `entry` and `route` roles.

### B1.3 Framework-aware connections

New edge kinds and symbols that answer the questions novices ask most:

- **HTTP routes**: method + path as a symbol (`kind: route`, e.g. `GET /customers/{id}`) attached to
  the handler function, for FastAPI, Flask, Django URLconf, Express, Gin/net-http. Edge `handles`
  from route to handler.
- **Environment and config keys**: `os.environ[...]`, `os.getenv`, `process.env.X`, `viper`/`os.Getenv`
  become `references` edges to a synthetic `env:<NAME>` node, and dotenv/YAML/TOML files that define
  the key get an `defines` edge. The env node card lists who reads and who sets it.
- **SQL in code**: parse string literals that look like SQL (`SELECT|INSERT|UPDATE|DELETE|CREATE`)
  with a small statement grammar to extract table names, replacing the current substring match.
  Edges stay `references` but become `exact`.
- **CLI commands**: argparse/click/typer subcommands and cobra commands as `kind: command` symbols
  with `handles` edges to their functions.

### B1.4 Better call resolution without a language server

- **Receiver tracking** inside a file: when `x = Foo()` or `x: Foo` or `x = module.make()` precedes
  `x.bar()`, resolve `bar` on `Foo` (or on the return type when annotated) instead of by name alone.
  Covers the common Python/JS/Go cases; anything unresolved stays `heuristic`.
- **Full re-export chains**: today one level; follow `__init__` and `index.ts` re-exports to any depth
  with a cycle guard.
- **Scope-aware names**: a local variable or parameter shadowing a symbol name must not produce a
  call edge.
- **tsconfig `paths` aliases** for TS/JS.

### B1.5 Tests to code

`tests` role plus imports and name convention (`test_load` -> `load`, `TestLoader` -> `Loader`) give
`tests` edges from test functions to the symbols under test. Cards show "Tested by"; symbols with
no test are visible as such.

### B1.6 Git signals

Per file: last change date, commits in the last 90 days, top authors (count only), and co-change
partners (files changed in the same commits more than N times). Stored in `scans/<repo>/<branch>/git.json`
(separate from `local.json` because it changes with history, not content, and must not break the
deterministic scan). Cards show "last changed", "changes often", "usually changed together with".

### B1.7 Generated and vendored detection, metrics

Mark `generated` by headers ("DO NOT EDIT", "generated by"), minification, protobuf/thrift
patterns, lockfiles; mark `vendored` by path. Per symbol: line count and a cheap cyclomatic
estimate (branch keywords). Per file: symbol count, import fan-in and fan-out (from edges). Shown as
small facts, used by the AI Scan to size packets.

### B1.8 Optional language-server tier

Auto-detected, never required: if `pyright`, `gopls`, or `typescript-language-server` is on PATH,
`scan/lsp.py` asks it for go-to-definition on every unresolved or heuristic call site after the
tree-sitter pass and upgrades matching edges to `exact` with `note: "lsp"`. Results are cached per
content hash. Settings gain an "Use language servers when available" switch (default on) and the
project page reports which servers were used. LSP output must never remove tree-sitter edges, only
confirm or add, so the offline result is identical whether or not a server is present except for
confidence levels.

### B1.9 What stays with the AI

Why a file exists, which of several similar helpers to edit, judgement about mismatches and risks,
plain-language explanation for a novice. Scripts feed these; they do not replace them.

## B2. AI efficiency: packets first, agent second

### B2.1 Briefing packets

`ai/packets.py` builds, per file, a self-contained text packet from scan data only:

1. File facts: path, roles, title hint, header doc, metrics, imports (resolved), incoming and
   outgoing edges with the *already finished* one-line summaries of the other end.
2. Symbols: id, signature, docstring, and the **body text only when the docstring is missing or the
   symbol is an entry point or route**; otherwise the signature is enough.
3. For files with a role template (B2.2), the draft summary.
4. The question: fill or correct the `AiFileSummary` JSON.

Packets are built leaf-first so the summaries of imported files are available to their importers.
Packet size is capped (default 12k tokens); oversized files are split by symbol groups and merged.

### B2.2 Skip and template

Files that need no model at all get a template summary written by `ai/templates.py` with
`model: "template"`: empty `__init__.py` ("Package marker"), pure re-export files, lockfiles,
`generated` and `vendored` files, trivial config. They still show a title and purpose on the card
and still count as summarised. The AI Scan lists them separately so the user can force them.

### B2.3 Drafts, not blanks

When B1.1 and B1.2 already supply a title and purpose, the packet says so and asks the model to
return only fields it changes (`{"keep": true}` or a partial object). Validation merges partials
into the draft before saving, so the stored file is always a complete `AiFileSummary`.

### B2.4 Packet calls instead of agent turns

Each packet is one `query()` call with no tools, `max_turns=1`, a JSON `output_format` schema, and
`max_budget_usd` per call. Calls run concurrently (default 4 at a time) through the same validation
as the agent's submit tool. A packet result can set `"unclear": true` with a reason; those files go to
the agent stage.

### B2.5 Agent for the hard cases and the overview

The existing agent session keeps its tools but receives only: the unclear files, the files whose
packets failed validation twice, and the repo overview task. It gets the finished summaries as
context instead of reading everything again. Expected agent share: a small fraction of files.

### B2.6 Model tiers

Settings gain `packet_model` (default `claude-haiku-4-5`) next to `model` (default
`claude-sonnet-5`, used for the agent stage, overview, diff explanation and AI search). Each stored
summary records which model wrote it; the card shows a small marker for template and packet
summaries so the user can request a stronger pass.

### B2.7 Incremental, with neighbour invalidation

Content-hash skipping stays. In addition, when a file's symbols or edges change (signature added,
import removed), importers whose summaries mention it are marked `stale` rather than redone; the
card shows "may be outdated" and a later run refreshes stale files first. Avoids re-paying for the
whole repo after one edit while not silently lying.

### B2.8 Stable prompts and measured costs

System prompt and skill text are byte-stable across a run so prompt caching applies. Every call
logs input/output tokens, cost, model, and stage to `scans/<repo>/<branch>/ai/_log.jsonl`; the
project page shows cost per file class (template/packet/agent) so each change in B1 or B2 can be
checked against numbers.

### B2.9 Skills to add

- `metis-packet-summary`: how to fill a summary from a packet, when to answer "keep", when to flag
  "unclear" (and why that is cheap, not a failure).
- Updated `metis-file-summary` for the agent: assume packets exist; read only what the packet could
  not show.

## B3. Data model changes

- `FileEntry`: `header_doc`, `roles`, `title_hint`, `generated`, `vendored`, `metrics`.
- `Symbol`: `docstring` for all languages, `metrics`, new kinds `route`, `command`.
- `Edge`: new kinds `handles`, `defines`, `tests`; `note` already exists for `lsp`.
- New node id prefix `env:<NAME>` for environment/config keys.
- `GitSignals` model in `git.json`.
- `AiFileSummary`: `model` may be `"template"`; new `stale: bool`, `source: "template" | "packet" | "agent"`.
- `Settings`: `packet_model`, `packet_concurrency`, `use_language_servers`.
- `AiIndex`: per-source counts and cost.

Schema version bumps to 2; old files load with defaults.

## B4. Phases

| # | Phase | Deliverable | Verify by |
|---|---|---|---|
| 13 | Docs and roles | B1.1 doc extraction for all supported languages, B1.2 roles and title hints, cards use them | Fixture symbols in JS, Go, Bash, SQL carry docstrings; `cli.py` has role `entry`, `ci.yml` role `ci` |
| 14 | Framework edges | B1.3 routes, env keys, SQL parsing, CLI commands | New fixture `webproj` (FastAPI + Express + dotenv) produces route, env and exact table edges |
| 15 | Call precision | B1.4 receiver tracking, deep re-exports, scope awareness, tsconfig paths | `Loader().load()` resolves exact; shadowed names produce no edge |
| 16 | Tests, git, metrics | B1.5, B1.6, B1.7 | `test_load` -> `load` edge; git.json on the fixture; generated file skipped |
| 17 | Templates and packets | B2.1, B2.2, B2.3, B2.4, B2.6, B2.8 | Packets built offline and golden-tested; one real packet run on the demo repo, cost compared with the v1 agent run |
| 18 | Agent demotion | B2.5, B2.7, B2.9 | Real run on the METIS repo itself: agent handles only unclear files; stale marking after an edit |
| 19 | Language servers | B1.8 | With pyright installed, heuristic edges on the METIS repo drop measurably; without it, scan output unchanged |

Phases 13 to 16 and 19 need no AI. 17 comes before 18 because the agent stage is defined by what
packets leave behind. Target for the demo run after 17 and 18: well under half the v1 cost per file,
with most files never read in full by a model.

## B5. Not doing

- Bundling language servers (decided: optional, auto-detected).
- Type inference beyond single-file receiver tracking; that is what the LSP tier is for.
- Embedding-based search: the two-stage AI search covers the need until the index outgrows one call.

## B6. Decision log

Resolved 2026-09-24:
1. Private GitHub repos: optional token per repo, stored outside project files.
2. Import resolution order: Python/JS/TS/Go first, then YAML, Bash, SQL.
3. AI budget cap 5 USD per run, editable.
4. Default model `claude-sonnet-5`, editable.
5. Branches coexist via worktrees.

Resolved 2026-09-25:
6. Tabs are rows of cards encoded in the URL; no server state.
7. AI search is two-stage: summaries call first, code agent on request.
8. Source highlighting is Pygments server-side; the toggle is a CSS class.

Resolved 2026-10-01:
9. Language servers are an optional, auto-detected precision tier; never required, never bundled (B1.8).
10. Two model settings: a cheap packet model (default `claude-haiku-4-5`) and the main model for agent work, overview, diff and search (B2.6).
11. PLAN.md is kept as current state plus roadmap rather than a growing history; superseded detail lives in git history.
