# METIS

METIS turns a codebase into something you can click through like a wiki. Point it at one or more
repositories and it builds a catalogue of every file, function, class, route, table and configuration
key, works out how they connect, and shows all of that in a browser as a row of cards. Optionally it
asks Claude to explain, in plain language, what each file is for and where to look before you change
something.

It was built for novice programmers facing an unfamiliar codebase, and it stays useful for senior
developers who want a fast, offline map of a project and its cross-repository dependencies.

**If you are new to programming:** you don't need to understand how METIS works to use it. Add a
project, click "Add and scan", then click around. Every card has a "Source" section if you want to
see the actual code, and the AI explanations are written for you.

**If you are an experienced developer:** everything in the Local Scan is deterministic, offline, and
stored as JSON you can inspect. The AI layer is optional and sits on top. Section [How the Local Scan
works](#how-the-local-scan-works) describes the exact rules.

---

## Contents

1. [Requirements](#requirements)
2. [Install and first run](#install-and-first-run)
3. [The workflow](#the-workflow)
4. [The browser](#the-browser)
5. [What a card shows](#what-a-card-shows)
6. [How the Local Scan works](#how-the-local-scan-works)
7. [AI features](#ai-features)
8. [Comparing branches](#comparing-branches)
9. [Settings](#settings)
10. [Command line](#command-line)
11. [Where your data lives and what leaves your machine](#where-your-data-lives-and-what-leaves-your-machine)
12. [Limitations](#limitations)
13. [Development](#development)
14. [Glossary](#glossary)

---

## Requirements

- **Python 3.12** and [uv](https://docs.astral.sh/uv/) (a fast Python package manager; `uv sync`
  installs everything else).
- **git** on your PATH.
- A web browser. METIS runs as a small local web server and opens a tab for you.
- **For the AI features only:** [Claude Code](https://claude.com/claude-code) installed and logged in
  (`claude auth status` should say `loggedIn: true`). Without it, everything except the AI buttons works.
- **Optional:** a language server for extra precision (`pyright`, `gopls`, or
  `typescript-language-server`). METIS uses one automatically when it finds it on PATH.

## Install and first run

```
git clone <this repository>
cd metis
uv sync
uv run metis serve
```

`metis serve` starts the app at http://127.0.0.1:8765 and opens your browser. Stop it with Ctrl-C.

Data is kept in `~/.metis` (see [Where your data lives](#where-your-data-lives-and-what-leaves-your-machine)).
To experiment without touching that folder, set `METIS_HOME=/some/other/dir` before any command.

## The workflow

1. **Add New Project.** A project is a named group of repositories. Put related repositories in one
   project so METIS can show how they depend on each other.
2. **Add repository.** Paste a GitHub URL or a path to a folder on your disk. For a private GitHub
   repository, add a personal access token in the second field. METIS clones the repository (or
   references the local folder in place) and runs a **Local Scan** immediately.
3. **Browse.** Click a repository, folder or file in the tree on the left. Each click opens a card to
   the right. Inside a card, click a function, an import, a connection or a name in the source to keep
   following the code.
4. **AI Scan** (optional). On the project page, press AI Scan next to a repository. Claude reads the
   scan facts and the code and writes a short explanation for every file and function. Cards then show
   those explanations in place of the raw facts.
5. **Diff.** Pick two branches of a repository to see what changed, with every changed file linked to
   its card, and ask Claude to explain the change set.

Branches: each repository has a branch selector on the project page. Switching branch checks that
branch out into its own working copy and scans it, so several branches can be browsed side by side
without disturbing each other.

## The browser

The browse page is a **column browser** (like the column view in the macOS Finder).

```
+-------------+  +----------------------+  +----------------------+
| repo tree   |  | main.py              |  | core.py              |
| .github/    |  | Entry point - Python |  | Core loading logic   |
| app/        |  |                      |  |                      |
| [main.py]   |--| [import core.py]     |--| def load(...)        |
| readme.md   |  | def main()           |  | class Loader         |
+-------------+  +----------------------+  +----------------------+
```

- **Clicking an item in a card opens the next card to its right.** Cards further right are replaced,
  so the row always reads as one path through the code. The highlighted item in each card shows how
  you got to the next one.
- **Tabs.** The tab bar above the cards keeps several rows. "+" opens an empty row, "×" closes one.
  **Ctrl-click (Cmd-click on a Mac) or middle-click** any link to open it in a new tab that keeps the
  current path up to that card. Tabs live in the page URL, so reload, back/forward and bookmarks
  restore them.
- **Search.** Type in the box above the tree. Results appear as the first card, grouped into files,
  functions, classes, tables and keys. Click a result to continue from it.
- **AI search.** Press the "AI" button next to the search box and the box becomes a question field:
  "the function that triggers the backup", "where are the secrets stored". Press Enter. Claude answers
  with a card of likely places and a reason for each. See [AI search](#ai-search).
- **Source view.** Every file and function card has a collapsible "Source" section with line numbers
  and a "Highlight" checkbox for syntax colouring. Names in the source are clickable: a function
  definition opens its own card, a call opens what is called. A dashed underline means METIS only
  guessed the target; a dotted underline means it could not resolve the name. "line 42" in any
  connection list jumps to that line in the source.
- **Theme.** Dark by default; switch to light on the Settings page.

## What a card shows

Every card is one **node**: a repository, a folder, a file, a symbol (function, class, method, route,
table, ...), an environment key, or an external dependency.

Before an AI Scan a file card shows what the scan found: a title such as "Entry point - Python" or
"CI workflow - GitHub Actions", the author's own header comment if there is one, the file's roles, how
many files use it and how many it uses, when it last changed, which files usually change together with
it, the symbols it defines (with their documentation comments), its imports, and two lists:

- **Uses**: what this file or function calls, references, runs or includes, with the line number.
- **Used by**: who imports, calls, tests or runs it.

Connections marked *(guess)* were matched by name only; everything else was resolved from an import
or a definition the scan is sure about.

After an AI Scan the same card gains a plain-language title and purpose, a one-to-three-sentence
explanation per function with where its data comes from and goes to, confirmed connections with a
reason, and a "Before you edit" note when there is something you should know. The card's subtitle
says which stage produced the summary (template, packet or agent) and flags summaries that may be
outdated because a neighbouring file changed.

A **symbol card** shows the explanation or documentation comment, the code of that symbol, its members
(methods of a class), and the same Uses/Used by lists. A **repository card** shows where execution
starts, the HTTP routes, the environment keys, the top-level folders and the external dependencies,
plus the AI overview once one exists. An **environment key card** lists who sets the key (dotenv
files, compose files, CI configuration, shell exports) and who reads it.

## How the Local Scan works

The Local Scan runs entirely on your machine, needs no network, and produces the same JSON for the
same commit every time. It is the foundation for everything else.

**Files.** Git-tracked files (respecting `.gitignore`), or a plain directory walk with a default ignore
list for folders that are not repositories. Binary files and files over the size cap (Settings) are
listed but not parsed.

**Symbols.** Functions, classes, methods, constants and module variables are extracted with
[tree-sitter](https://tree-sitter.github.io/) grammars for every language the
`tree-sitter-language-pack` ships a tags query for (Python, JavaScript, TypeScript, Go, Rust, Java,
Kotlin, C, C++, C#, Ruby, PHP, Swift, Lua, ...). YAML keys, SQL tables/views/procedures and shell
functions come from small dedicated extractors. HTTP routes and CLI commands are symbols too (see
below).

**Documentation.** The comment block directly above a definition becomes that symbol's documentation,
in any language (JSDoc, Go doc comments, Rust `///`, Javadoc, `#` and `--` runs). A comment at the top
of the file becomes the file's header line, unless it sits directly above the first definition.
Python docstrings are read as well.

**Roles.** Each file is classified by path, name and content: `entry` (something starts here),
`route`, `test`, `migration`, `schema`, `model`, `ci`, `config`, `build`, `script`, `doc`, `generated`,
`vendored`. Roles give the title hint on cards and tell the AI Scan which files need no model at all.

**Imports.** Resolved per language, in this order: same repository, another repository in the same
project (matched by package name, `package.json` name or Go module path), otherwise external.
Python handles relative imports, packages and re-exports through `__init__.py`; JavaScript and
TypeScript handle relative paths, index files, `@/` and `tsconfig` path aliases; Go resolves packages
to directories. Standard-library imports are listed but produce no connection.

**Connections (edges).** Each has a kind and a confidence:

| kind | meaning | how it is found |
|---|---|---|
| imports | file imports a file, folder, symbol or package | resolved import statements |
| calls | a function calls another | call sites matched to definitions |
| inherits | a class extends another | base-class references |
| handles | a route or CLI command runs a function | decorator or registration patterns |
| references | code refers to a table or environment key | parsed SQL in string literals; `os.environ`, `process.env`, `os.Getenv`, ... |
| defines | a config file sets an environment key | `.env` files, `environment:`/`env:` sections in YAML, shell `export` |
| tests | a test function exercises a symbol | `test_load` -> `load`, `TestLoader` -> `Loader`, by import first |
| runs / includes | a script or workflow invokes or sources a file | YAML `run:` commands, shell `source` and invocations |

A **call** is resolved with increasing uncertainty: `self.x()` to the class's own method; `obj.x()`
through the variable's type when it was assigned from a constructor or annotated; `module.x()` through
the import alias; then a unique match in the same file; then in imported files (following re-exports);
then anywhere in the repository if the name is unique, marked *heuristic*. A parameter or local
variable that shadows a name blocks the edge. Heuristic edges are shown as guesses and the AI Scan can
reject them.

**Git signals.** From `git log`: last change date, changes in the last 90 days, number of authors, and
the files most often changed in the same commits. Kept in a separate file so they never affect the
deterministic scan output.

**Metrics.** Lines and a branch count per function; symbol count, fan-in and fan-out per file.

**Language servers (optional).** When `pyright`, `gopls` or `typescript-language-server` is on PATH
and the Settings switch is on, METIS asks it for go-to-definition on every call site it could only
guess, and upgrades those edges to exact. It never removes anything the scan found, so results without
a server differ only in confidence levels. On METIS's own source this turned 107 guesses into exact
links in about 25 seconds.

## AI features

All AI features go through the Claude Agent SDK, which uses your Claude Code login. Nothing runs
until you press a button, and every run has a cost cap you set in Settings.

### AI Scan

The scan has three stages, from cheapest to most capable, and each file is handled by the first stage
that can explain it:

1. **Templates (no model, free).** Package markers, pure re-export files, lockfiles, generated and
   vendored code and `.env` files get a fixed summary written from scan facts.
2. **Packets (one small call per file).** For each remaining file METIS builds a "briefing packet":
   roles, documentation comments, imports with the already-finished summaries of their targets,
   connections in and out, and the body of any function that has no documentation. One call on the
   packet model (default `claude-haiku-4-5`) with no tools and a strict JSON output schema turns the
   packet plus a draft summary into the final one. Files are processed leaves first and several at a
   time, so later files see their dependencies' summaries. The model may answer "keep" if the draft is
   already right, or "unclear" if the packet is not enough.
3. **Agent (full access, main model).** A read-only agent session on the main model (default
   `claude-sonnet-5`) handles only the files the packet stage flagged as unclear, then writes the
   repository overview. It can read any file and query the scan through METIS's own tools, and it can
   only save results through validating tools, so a malformed answer never reaches disk.

Re-runs are incremental: a file is only redone when its content changed, or when a file it depends on
changed its functions or imports (the card says "may be outdated" until then). "redo all" forces a
full run. The project page shows how many files each stage produced and what they cost; every model
call is also appended to a log you can inspect (`scans/<repo>/<branch>/ai/_log.jsonl`).

On a small sample repository a full run cost about $0.25: a third of the files free, the rest at
roughly two cents each, plus the overview.

### AI search

Stage 1 sends a compact index of the project (file titles and purposes, symbol names) with your
question to one no-tool call and gets back a ranked list of node ids with reasons. It costs cents and
takes seconds. If the answer says "needs code" or you are not satisfied, **Search deeper** starts a
read-only agent that greps and reads the code. Results are cached per question until the project is
rescanned, and each card shows its cost.

### Explain changes

On the Diff page, **Explain changes** sends the diff, the commit messages and the known summaries of
the changed files to Claude and stores a Markdown explanation: what changed in one sentence, what and
why grouped by concern, how the pieces connect, and what to check before merging.

## Comparing branches

The Diff page lets you pick a repository and two branches. It lists changed files with added and
removed line counts (each linked to its card), shows the commits between the branches, and the unified
diff per file. The AI explanation, if requested, appears above the list.

## Settings

| setting | default | what it does |
|---|---|---|
| Theme | dark | dark or light |
| AI model | `claude-sonnet-5` | agent stage, overview, diff explanation, AI search |
| Packet model | `claude-haiku-4-5` | the per-file packet calls; cheaper and faster |
| Packet calls in parallel | 4 | how many packet calls run at once |
| Use language servers | on | refine call edges with pyright/gopls/tsserver when installed |
| Budget cap per AI Scan run | $5 | hard stop for one run of one repository |
| Max agent turns | 400 | backstop for the agent stage |
| Largest file to parse | 1 MB | bigger files are listed, not parsed |
| Web port | 8765 | takes effect on the next start |

## Command line

Everything the GUI does can be scripted:

```
uv run metis serve [--port N] [--no-browser]      # start the web app
uv run metis projects                             # list projects
uv run metis create NAME                          # new project
uv run metis add PROJECT URL_OR_PATH [--token T]  # add a repository and scan it
uv run metis scan PROJECT [--repo R] [--branch B] # Local Scan (all repos by default)
uv run metis ai-scan PROJECT REPO [--force]       # AI Scan
uv run metis explain-diff PROJECT REPO BASE HEAD  # print an AI explanation of a diff
```

## Where your data lives and what leaves your machine

Everything is under `~/.metis` (or `METIS_HOME`):

```
settings.json                        your settings
secrets.json                         GitHub tokens, readable only by you (mode 600)
projects/<name>/project.json         the project and its repositories
projects/<name>/repos/<repo>/        clones of GitHub repositories
projects/<name>/worktrees/...        extra branches you opened
projects/<name>/scans/<repo>/<branch>/local.json   the Local Scan (JSON)
projects/<name>/scans/<repo>/<branch>/git.json     git signals
projects/<name>/scans/<repo>/<branch>/ai/*.json    one AI summary per file, plus the overview and log
```

Local folders you add are referenced in place, never copied. Tokens are passed to git per command and
never written into the clone's configuration.

The Local Scan sends nothing anywhere. The AI features send code and scan facts to Anthropic through
your Claude Code login: the AI Scan sends the files it summarises (bodies of undocumented functions in
packets, whole files in the agent stage), AI search sends an index of names and summaries, and Explain
changes sends the diff. Nothing is sent until you press one of those buttons.

## Limitations

- Call resolution without a language server is heuristic for dynamic code (duck typing, decorators
  that rewrite functions, metaprogramming). Guesses are marked as such.
- Import resolution covers Python, JavaScript/TypeScript and Go in depth; other languages get symbols
  and documentation but their imports are listed as plain text.
- Routes and CLI commands are recognised for common frameworks (FastAPI, Flask, Django, Express,
  NestJS decorators, Gin, net/http, argparse, click, typer, cobra). Others are not yet.
- The AI explanations are only as good as the model's reading; they say "may be outdated" after
  neighbouring changes but are not re-verified automatically.
- Branch switching for a local folder repository creates a worktree registered in that repository's
  own `.git`; removing the project prunes it.
- Single user, local only. There is no authentication on the web server; it binds to 127.0.0.1.

## Development

```
uv sync
uv run pytest -q                              # full suite, no network, no AI (~5 s)
uv run ruff check src tests && uv run ruff format src tests
```

Layout under `src/metis/`: `model/` (Pydantic models, the JSON contract, and the node id scheme),
`projects/` (project registry and git), `scan/` (walker, tree-sitter parsing, symbols, docs, roles,
framework facts, import handlers, linker, git signals, language-server tier), `ai/` (AI Scan stages,
tools, prompts, skills, search, cost log), `store.py` (the only read path for the UI), `render/`
(cards as Markdown, source view), `web/` (FastAPI routes, templates, the column browser script).
`CLAUDE.md` holds the invariants and `PLAN.md` the design and decision log.

Tests use tiny real repositories in `tests/fixtures/`; add a case there rather than mocking the
scanner. To add a language, map its extension in `scan/walker.py`; symbols work as soon as the
grammar pack has a tags query for it, and an import handler in `scan/imports/` adds connections.

## Glossary

- **Repository (repo):** a folder of code tracked by git. A GitHub URL points at one.
- **Branch:** a named line of development inside a repository. METIS keeps one working copy per branch
  you open.
- **Symbol:** something with a name that the code defines: a function, class, method, constant, table,
  route.
- **Connection (edge):** a relationship between two things, such as "file A imports file B" or
  "function X calls function Y". *Exact* means the scan is sure; *heuristic* means it matched by name.
- **Node:** anything that can be a card: a repository, folder, file, symbol, environment key or
  external package. Every node has an id like `s:repo:path/to/file.py#Class.method`.
- **Local Scan:** the offline analysis METIS runs on its own.
- **AI Scan:** the optional pass where Claude writes explanations on top of the Local Scan.
- **Packet:** the bundle of facts about one file that the AI Scan sends in a single cheap call.
- **Language server:** a program (pyright, gopls, tsserver) that understands one language precisely;
  editors use them for "go to definition". METIS borrows them for the same purpose when available.
- **Environment key:** a named setting read from the operating system environment, such as
  `DATABASE_URL`, typically defined in a `.env` file or in a deployment configuration.
