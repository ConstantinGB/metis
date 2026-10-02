---
name: metis-repo-overview
description: How to write the METIS repository overview.
---
## Writing the repository overview

Do this last, once every file is summarised, so you can draw on what you learned.

**title**: "<what it is> - <main technology>", e.g. "Billing API - FastAPI service".

**purpose**: two or three sentences: what the software does for its users, and who runs it (a service,
a CLI, a library other repos import, a set of scripts).

**entry_points**: the node ids where execution starts: `main` functions, CLI commands, web app factories,
scheduled jobs, the workflow files that run things. Three to eight ids.

**architecture**: Markdown for a novice, in this order:
1. The layers or main folders, one line each: what lives there and what it is allowed to talk to.
2. The two or three most important flows, as short numbered steps that name files and functions with
   their node ids in Markdown links: `[core.load](node:s:repo:alpha/core.py#load)`.
3. "Where to look first" for the most common kinds of change (adding an endpoint, changing a schema,
   fixing a bug in X).
4. Anything surprising: generated code, dynamic imports, files that must be edited together.
Keep it under 60 lines.
