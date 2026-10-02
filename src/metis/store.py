"""Read access to everything on disk for one project: scans, AI output, source text.

The web and render layers only talk to this module; they never call the scanner or AI.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections import defaultdict
from pathlib import Path

from metis.model import (
    AiFileSummary,
    AiIndex,
    AiRepoOverview,
    DiffExplanation,
    Edge,
    FileEntry,
    GitSignals,
    LocalScan,
    Project,
    Repo,
    Symbol,
    parse_id,
)
from metis.projects import registry
from metis.scan.runner import git_json_path, load_git_signals, load_scan, scan_json_path

# --------------------------------------------------------------------------- ai file paths


def ai_dir(project_name: str, repo_name: str, branch: str) -> Path:
    d = registry.scans_dir(project_name, repo_name, branch) / "ai"
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_slug(path: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "__", path)[:80]
    return f"{safe}-{hashlib.sha1(path.encode()).hexdigest()[:8]}"


def ai_summary_path(project_name: str, repo_name: str, branch: str, path: str) -> Path:
    return ai_dir(project_name, repo_name, branch) / f"{file_slug(path)}.json"


def ai_overview_path(project_name: str, repo_name: str, branch: str) -> Path:
    return ai_dir(project_name, repo_name, branch) / "_overview.json"


def ai_index_path(project_name: str, repo_name: str, branch: str) -> Path:
    return ai_dir(project_name, repo_name, branch) / "_index.json"


def diff_path(project_name: str, repo_name: str, base: str, head: str) -> Path:
    d = registry.project_dir(project_name) / "scans" / repo_name / "diffs"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{registry.branch_slug(base)}..{registry.branch_slug(head)}.json"


def load_ai_summary(
    project_name: str, repo_name: str, branch: str, path: str
) -> AiFileSummary | None:
    p = ai_summary_path(project_name, repo_name, branch, path)
    if not p.exists():
        return None
    try:
        return AiFileSummary.model_validate_json(p.read_text())
    except ValueError:
        return None


def save_ai_summary(project_name: str, repo_name: str, branch: str, summary: AiFileSummary) -> Path:
    p = ai_summary_path(project_name, repo_name, branch, summary.path)
    p.write_text(summary.model_dump_json(indent=2))
    return p


def load_ai_overview(project_name: str, repo_name: str, branch: str) -> AiRepoOverview | None:
    p = ai_overview_path(project_name, repo_name, branch)
    if not p.exists():
        return None
    try:
        return AiRepoOverview.model_validate_json(p.read_text())
    except ValueError:
        return None


def save_ai_overview(project_name: str, repo_name: str, branch: str, ov: AiRepoOverview) -> None:
    ai_overview_path(project_name, repo_name, branch).write_text(ov.model_dump_json(indent=2))


def load_ai_index(project_name: str, repo_name: str, branch: str) -> AiIndex:
    p = ai_index_path(project_name, repo_name, branch)
    if p.exists():
        try:
            return AiIndex.model_validate_json(p.read_text())
        except ValueError:
            pass
    return AiIndex()


def save_ai_index(project_name: str, repo_name: str, branch: str, index: AiIndex) -> None:
    ai_index_path(project_name, repo_name, branch).write_text(index.model_dump_json(indent=2))


def load_diff_explanation(
    project_name: str, repo_name: str, base: str, head: str
) -> DiffExplanation | None:
    p = diff_path(project_name, repo_name, base, head)
    if not p.exists():
        return None
    try:
        return DiffExplanation.model_validate_json(p.read_text())
    except ValueError:
        return None


def save_diff_explanation(project_name: str, repo_name: str, expl: DiffExplanation) -> None:
    diff_path(project_name, repo_name, expl.base, expl.head).write_text(
        expl.model_dump_json(indent=2)
    )


# --------------------------------------------------------------------------- project store


class ProjectStore:
    """Caches the project's scans in memory and reloads them when the files change."""

    def __init__(self, project_name: str):
        self.name = project_name
        self._project: Project | None = None
        self._project_mtime = 0.0
        self._scans: dict[
            str, tuple[float, LocalScan, dict[str, FileEntry], dict[str, Symbol]]
        ] = {}
        self._in: dict[str, list[Edge]] = {}
        self._out: dict[str, list[Edge]] = {}
        self._index_key: tuple = ()
        self._loose: dict[str, dict[str, str]] = {}

    # -- project ------------------------------------------------------------

    @property
    def project(self) -> Project:
        f = registry.project_file(self.name)
        mtime = f.stat().st_mtime if f.exists() else 0.0
        if self._project is None or mtime != self._project_mtime:
            self._project = registry.load_project(self.name)
            self._project_mtime = mtime
        return self._project

    def repo(self, name: str) -> Repo | None:
        try:
            return self.project.repo(name)
        except KeyError:
            return None

    # -- scans --------------------------------------------------------------

    def scan(self, repo_name: str, branch: str | None = None) -> LocalScan | None:
        repo = self.repo(repo_name)
        if repo is None:
            return None
        branch = branch or repo.active_branch
        path = scan_json_path(self.name, repo_name, branch)
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        key = f"{repo_name}@{branch}"
        cached = self._scans.get(key)
        if cached is None or cached[0] != mtime:
            scan = load_scan(path)
            if scan is None:
                return None
            self._scans[key] = (mtime, scan, scan.file_by_path(), scan.symbols_by_id())
        return self._scans[key][1]

    def all_scans(self) -> list[LocalScan]:
        return [s for s in (self.scan(r.name) for r in self.project.repos) if s is not None]

    def _ensure_index(self) -> None:
        scans = self.all_scans()
        key = tuple((s.repo, s.branch, s.scanned_at.isoformat()) for s in scans)
        if key == self._index_key:
            return
        inn: dict[str, list[Edge]] = defaultdict(list)
        out: dict[str, list[Edge]] = defaultdict(list)
        for s in scans:
            for e in s.edges:
                out[e.source].append(e)
                inn[e.target].append(e)
        self._in, self._out, self._index_key = dict(inn), dict(out), key

    def incoming(self, node_id: str) -> list[Edge]:
        self._ensure_index()
        return self._in.get(node_id, [])

    def outgoing(self, node_id: str) -> list[Edge]:
        self._ensure_index()
        return self._out.get(node_id, [])

    # -- nodes --------------------------------------------------------------

    def file(self, node_id: str) -> FileEntry | None:
        ref = parse_id(node_id)
        if ref.kind not in ("f", "s") or not ref.repo or ref.path is None:
            return None
        if self.scan(ref.repo) is None:
            return None
        key = f"{ref.repo}@{self.repo(ref.repo).active_branch}"  # type: ignore[union-attr]
        return self._scans[key][2].get(ref.path)

    def symbol(self, node_id: str) -> Symbol | None:
        ref = parse_id(node_id)
        if ref.kind != "s" or not ref.repo or self.scan(ref.repo) is None:
            return None
        key = f"{ref.repo}@{self.repo(ref.repo).active_branch}"  # type: ignore[union-attr]
        return self._scans[key][3].get(node_id)

    def dir_entries(self, repo_name: str, path: str) -> tuple[list[str], list[FileEntry]]:
        scan = self.scan(repo_name)
        if scan is None:
            return [], []
        prefix = path.strip("/")
        subdirs: set[str] = set()
        files: list[FileEntry] = []
        for f in scan.files:
            if prefix and not f.path.startswith(prefix + "/"):
                continue
            rest = f.path[len(prefix) + 1 :] if prefix else f.path
            if "/" in rest:
                subdirs.add(rest.split("/", 1)[0])
            else:
                files.append(f)
        return sorted(subdirs), files

    def checkout(self, repo_name: str) -> Path | None:
        repo = self.repo(repo_name)
        if repo is None:
            return None
        p = repo.checkouts.get(repo.active_branch)
        return Path(p) if p else None

    def read_source(self, repo_name: str, path: str) -> str | None:
        root = self.checkout(repo_name)
        if root is None:
            return None
        try:
            return (root / path).read_text(errors="replace")
        except OSError:
            return None

    def read_lines(self, repo_name: str, path: str, start: int, end: int) -> str | None:
        text = self.read_source(repo_name, path)
        if text is None:
            return None
        lines = text.splitlines()
        return "\n".join(lines[max(start - 1, 0) : end])

    def git_signals(self, repo_name: str) -> GitSignals | None:
        b = self._branch(repo_name)
        return load_git_signals(git_json_path(self.name, repo_name, b)) if b else None

    # -- ai -----------------------------------------------------------------

    def _branch(self, repo_name: str) -> str | None:
        repo = self.repo(repo_name)
        return repo.active_branch if repo else None

    def ai_summary(self, repo_name: str, path: str) -> AiFileSummary | None:
        b = self._branch(repo_name)
        return load_ai_summary(self.name, repo_name, b, path) if b else None

    def ai_overview(self, repo_name: str) -> AiRepoOverview | None:
        b = self._branch(repo_name)
        return load_ai_overview(self.name, repo_name, b) if b else None

    def ai_index(self, repo_name: str) -> AiIndex:
        b = self._branch(repo_name)
        return load_ai_index(self.name, repo_name, b) if b else AiIndex()

    # -- loose resolution ---------------------------------------------------

    def resolve_loose(self, repo_name: str, text: str) -> str | None:
        """Turn a path, file name or symbol name written by the AI into a node id, if unambiguous."""
        text = text.strip()
        if not text:
            return None
        if re.match(r"^(env|[rdfsx]):", text):
            return text
        scan = self.scan(repo_name)
        if scan is None:
            return None
        key = f"{repo_name}@{scan.branch}@{scan.scanned_at.isoformat()}"
        index = self._loose.get(key)
        if index is None:
            index = {}
            counts: dict[str, int] = defaultdict(int)
            for f in scan.files:
                index[f.path] = f.id
                counts[posixpath.basename(f.path)] += 1
                for sym in f.symbols:
                    counts[sym.qualified_name] += 1
                    counts[sym.name] += 1
            for f in scan.files:
                base = posixpath.basename(f.path)
                if counts[base] == 1:
                    index.setdefault(base, f.id)
                for sym in f.symbols:
                    if counts[sym.qualified_name] == 1:
                        index.setdefault(sym.qualified_name, sym.id)
                    if counts[sym.name] == 1:
                        index.setdefault(sym.name, sym.id)
            self._loose = {key: index}
        cand = text[2:] if text.startswith("./") else text
        for c in (cand, cand.rstrip("/"), cand.replace("()", "")):
            if c in index:
                return index[c]
        if cand.rstrip("/") in {posixpath.dirname(f.path) for f in scan.files}:
            return f"d:{repo_name}:{cand.rstrip('/')}"
        return None

    # -- labels -------------------------------------------------------------

    def label(self, node_id: str, from_repo: str | None = None) -> str:
        try:
            ref = parse_id(node_id)
        except ValueError:
            return node_id
        prefix = "" if (ref.repo is None or ref.repo == from_repo) else f"{ref.repo}: "
        if ref.kind == "r":
            return ref.repo or node_id
        if ref.kind == "x":
            return ref.name or node_id
        if ref.kind == "env":
            return f"${ref.name}"
        if ref.kind == "d":
            return f"{prefix}{ref.path or '.'}/"
        if ref.kind == "f":
            return f"{prefix}{ref.path}"
        return f"{prefix}{ref.qualified_name} ({posixpath.basename(ref.path or '')})"


_STORES: dict[str, ProjectStore] = {}


def get_store(project_name: str) -> ProjectStore:
    store = _STORES.get(project_name)
    if store is None:
        store = _STORES[project_name] = ProjectStore(project_name)
    return store
