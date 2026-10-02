---
name: metis-explain-diff
description: How to explain a branch diff to a novice programmer.
---
## Explaining a change set

Write Markdown with these sections, each short:

1. **In one sentence**: what the change achieves from the user's point of view.
2. **What changed and why** — a bulleted list grouped by concern, not by file. Each bullet names the files
   involved and says what the change does. Prefer "adds a retry around the database call in `db.py`"
   over quoting the diff.
3. **How the pieces connect**: which changed files call or depend on each other, and whether behaviour of
   unchanged files is affected (a changed function signature, a renamed config key, a new table column).
4. **Things to check before merging**: risks, missing tests, migrations that must run, places that may
   need the same change. Skip this section if there is genuinely nothing.

Rules: plain words, no jargon without a gloss, no praise or judgement of the author, no speculation about
intent beyond what the code and commit messages show. If the diff was truncated, read the files to fill
the gaps rather than guessing.
