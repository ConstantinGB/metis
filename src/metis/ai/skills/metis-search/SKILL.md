---
name: metis-search
description: How to answer a plain-language "where is..." question over a METIS project index.
---
## Ranking search results

You get a project index (repos, files with their purpose when known, symbols with ids) and one
question such as "the function that triggers the backup" or "where are the secrets stored".

- Return node ids exactly as they appear in the index: `f:<repo>:<path>` for files,
  `s:<repo>:<path>#<qualified name>` for symbols, `d:<repo>:<dir>` for folders.
- Put the thing that *owns* the behaviour first (the definition), then callers, then mentions.
- Prefer a symbol over its file when the question is about a function or class; prefer the file
  when it is about configuration, data, or "where does X live".
- `why` is one sentence a novice can check ("defines `run_backup`, which `cli.py` calls on
  `--backup`"). No speculation beyond the index.
- Three to ten results. `confidence` is `high` only when a name or summary matches the question
  directly; `medium` for a good inference; `low` for a guess worth showing.
- Set `needs_code` to true when the index cannot answer well (no summaries for the likely files,
  or the question is about behaviour the names do not reveal).
