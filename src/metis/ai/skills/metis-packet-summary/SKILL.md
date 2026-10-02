---
name: metis-packet-summary
description: How to turn a METIS briefing packet into a file summary in one reply.
---
## Summarising from a packet

You receive everything the Local Scan knows about one file: roles, author documentation, imports
with the summaries of their targets, connections in and out, and the symbols with their bodies where
the documentation was missing. You also receive a draft summary assembled from those facts.

Answer with one JSON object:

- `keep: true` when the draft title and purpose are already right and complete. You may still add
  `summary.functions` for symbols the draft left unexplained.
- Otherwise `keep: false` and put only the fields you change in `summary`. Omitted fields keep the
  draft's values.
- `unclear: true` (with a one-line `reason`) when the packet cannot answer what the file is for: the
  bodies were truncated, behaviour hides in files you cannot see, or the file is glue whose meaning
  depends on callers. This is cheap and expected; an agent with full file access takes those files.

Writing rules are the same as for any METIS summary: titles like "Migration script - Python",
purposes that say why the file exists, one to three plain sentences per function, connections only
where the packet shows them, node ids copied exactly, heuristic targets that are wrong listed in
`rejected_targets`. Never invent ids.
