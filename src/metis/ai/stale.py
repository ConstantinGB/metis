"""Neighbour invalidation: when a file's interface changes, summaries of its importers go stale."""

from __future__ import annotations

from metis import store as st
from metis.model import FileEntry, LocalScan, parse_id


def _interface(f: FileEntry) -> tuple[frozenset[tuple[str, str]], frozenset[str]]:
    sigs = frozenset((s.qualified_name, s.signature) for s in f.symbols)
    targets = frozenset(i.resolved_to.id for i in f.imports if i.resolved_to)
    return sigs, targets


def changed_interfaces(previous: LocalScan, current: LocalScan) -> set[str]:
    old = previous.file_by_path()
    out: set[str] = set()
    for f in current.files:
        o = old.get(f.path)
        if o is None or o.content_hash == f.content_hash:
            continue
        if _interface(o) != _interface(f):
            out.add(f.path)
    return out


def mark_stale(
    project: str, repo: str, branch: str, previous: LocalScan, current: LocalScan
) -> list[str]:
    changed = changed_interfaces(previous, current)
    if not changed:
        return []
    by_path = current.file_by_path()
    importers: set[str] = set()
    for e in current.edges:
        tp = parse_id(e.target)
        if tp.repo == current.repo and tp.path in changed:
            sp = parse_id(e.source)
            if sp.path and sp.path != tp.path:
                importers.add(sp.path)
    marked: list[str] = []
    for path in sorted(importers):
        f = by_path.get(path)
        if f is None:
            continue
        summary = st.load_ai_summary(project, repo, branch, path)
        if summary is None or summary.content_hash != f.content_hash or summary.stale:
            continue  # pending anyway, or already marked
        summary.stale = True
        st.save_ai_summary(project, repo, branch, summary)
        marked.append(path)
    return marked
