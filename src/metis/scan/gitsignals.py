"""Per-file history signals from git log: recency, churn, authors, co-change partners."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from metis.model import FileGit, GitSignals
from metis.projects import git

MAX_COMMITS = 1500
MAX_FILES_PER_COMMIT = 30  # bigger commits (reformat, vendoring) say nothing about coupling
MIN_SHARED = 2
TOP_PARTNERS = 5


def collect(root: Path, repo: str, branch: str, known_files: set[str] | None = None) -> GitSignals:
    out = GitSignals(repo=repo, branch=branch)
    try:
        log = git.run_git(
            [
                "log",
                f"-n{MAX_COMMITS}",
                "--format=%x01%H%x00%at%x00%aE",
                "--name-only",
                "--no-renames",
            ],
            cwd=root,
            check=False,
        )
    except git.GitError:
        return out
    if not log.strip():
        return out
    cutoff = datetime.now(UTC) - timedelta(days=90)
    last: dict[str, datetime] = {}
    recent: Counter[str] = Counter()
    total: Counter[str] = Counter()
    authors: dict[str, set[str]] = defaultdict(set)
    pairs: Counter[tuple[str, str]] = Counter()
    for block in log.split("\x01"):
        if not block.strip():
            continue
        head, _, body = block.partition("\n")
        parts = head.split("\x00")
        if len(parts) < 3:
            continue
        ts = datetime.fromtimestamp(int(parts[1]), tz=UTC)
        author = parts[2]
        files = [f for f in body.splitlines() if f.strip()]
        if known_files is not None:
            files = [f for f in files if f in known_files]
        out.commits_seen += 1
        for f in files:
            total[f] += 1
            if ts >= cutoff:
                recent[f] += 1
            authors[f].add(author)
            if f not in last or ts > last[f]:
                last[f] = ts
        if 2 <= len(files) <= MAX_FILES_PER_COMMIT:
            fs = sorted(files)
            for i, a in enumerate(fs):
                for b in fs[i + 1 :]:
                    pairs[(a, b)] += 1
    partners: dict[str, Counter[str]] = defaultdict(Counter)
    for (a, b), n in pairs.items():
        if n >= MIN_SHARED:
            partners[a][b] = n
            partners[b][a] = n
    for f in total:
        out.files[f] = FileGit(
            last_changed=last.get(f),
            commits_90d=recent[f],
            commits_total=total[f],
            authors=len(authors[f]),
            co_changes=partners[f].most_common(TOP_PARTNERS),
        )
    return out
