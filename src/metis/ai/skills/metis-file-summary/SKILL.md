---
name: metis-file-summary
description: How to write a METIS file summary that a novice programmer can use.
---
## Writing a file summary

**title**: a label of the form "<what it is> - <language or framework>". Examples: "Migration script - Python",
"HTTP routes - FastAPI", "CI workflow - GitHub Actions", "Database schema - MySQL". Never just the filename.

**purpose**: one or two sentences answering "why does this file exist and what would break without it?".
Example: "Copies customer records from the legacy database into the new cluster, batch by batch, and logs
which ids were moved." Mention the main input and output when there is one.

**functions**: one entry per symbol that matters (functions, methods, classes, tables). Small private helpers
can be grouped under the function that uses them. For each:
- explanation: what it does and when it is called, in one to three plain sentences.
- inputs_from: node ids (or short names) of where its data comes from: other files, tables, config, HTTP.
- outputs_to: where its results go: return values used by a caller, files written, tables changed, requests sent.

**connections**: the file-level picture. `uses` for things this file depends on, `used_by` for things that
depend on it. `why` says what flows across: "reads the parsed config", "receives the list of ids to migrate".
Only include connections you saw in the code or that the Local Scan marked exact.

**rejected_targets**: if the Local Scan's outgoing edges include a heuristic target that is not real (for
example a function with the same name in an unrelated module), list that target id here.

**notes**: only what someone must know before editing: hidden side effects, order dependencies, things that
look unused but are loaded dynamically, environment variables it needs. Leave empty when there is nothing.

For documentation files (README, docs): the purpose is what the document covers; functions stays empty;
connections point at the files the document talks about.
For config and CI files: explain what each top-level section controls and which scripts it runs.

Most files are summarised beforehand from briefing packets (scan facts plus author documentation). You
only get the files the packet pass flagged as unclear: read exactly what the packet could not show
(callers, the unresolved parts) rather than re-reading everything. The finished summaries of other
files are available through metis_known_summaries.
