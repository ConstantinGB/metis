"""Shared context and the per-language handler interface."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Tree

from metis.model import Import, Reference, ResolvedTarget, Symbol, dir_id, file_id, repo_id
from metis.scan import walker


@dataclass
class RepoInfo:
    """What the scanner knows about another repo in the same project."""

    name: str
    package_names: list[str] = field(default_factory=list)
    go_module: str | None = None
    root: Path | None = None
    _files: set[str] | None = None
    _pkg_roots: dict[str, str] | None = None

    def files(self) -> set[str]:
        if self._files is None:
            self._files = set(walker.list_files(self.root)) if self.root else set()
        return self._files

    def python_package_roots(self) -> dict[str, str]:
        if self._pkg_roots is None:
            self._pkg_roots = python_package_roots(self.files())
        return self._pkg_roots


def python_package_roots(files: set[str]) -> dict[str, str]:
    """Map top-level python package name -> directory prefix ("" or "src", ...)."""
    roots: dict[str, str] = {}
    for f in files:
        if not f.endswith("/__init__.py"):
            continue
        parts = f.split("/")
        if len(parts) == 2:
            roots.setdefault(parts[0], "")
        elif len(parts) == 3:
            roots.setdefault(parts[1], parts[0])
    return roots


@dataclass
class ScanContext:
    repo: str
    root: Path
    files: set[str]
    dirs: set[str]
    package_names: list[str] = field(default_factory=list)
    go_module: str | None = None
    other_repos: list[RepoInfo] = field(default_factory=list)
    ts_paths: dict[str, str] = field(default_factory=dict)  # "@app/*" -> "src/app/*"
    _text_cache: dict[str, str | None] = field(default_factory=dict)
    _pkg_roots: dict[str, str] | None = None

    @classmethod
    def build(
        cls,
        repo: str,
        root: Path,
        files: list[str],
        package_names: list[str] | None = None,
        go_module: str | None = None,
        other_repos: list[RepoInfo] | None = None,
    ) -> ScanContext:
        dirs: set[str] = {""}
        for f in files:
            parts = f.split("/")
            for i in range(1, len(parts)):
                dirs.add("/".join(parts[:i]))
        return cls(
            repo=repo,
            root=root,
            files=set(files),
            dirs=dirs,
            package_names=package_names or [],
            go_module=go_module,
            other_repos=other_repos or [],
            ts_paths=_ts_paths(root),
        )

    def python_package_roots(self) -> dict[str, str]:
        if self._pkg_roots is None:
            self._pkg_roots = python_package_roots(self.files)
        return self._pkg_roots

    def read_text(self, rel: str) -> str | None:
        if rel not in self._text_cache:
            try:
                self._text_cache[rel] = (self.root / rel).read_text(errors="replace")
            except OSError:
                self._text_cache[rel] = None
        return self._text_cache[rel]

    # -- resolution helpers -------------------------------------------------

    def target_for_path(self, rel: str) -> ResolvedTarget | None:
        rel = posixpath.normpath(rel).lstrip("./") if rel not in ("", ".") else ""
        if rel.startswith("../"):
            return None
        if rel in self.files:
            return ResolvedTarget(kind="file", id=file_id(self.repo, rel))
        if rel in self.dirs:
            return ResolvedTarget(kind="dir", id=dir_id(self.repo, rel))
        return None

    def first_existing(self, candidates: list[str]) -> ResolvedTarget | None:
        for c in candidates:
            t = self.target_for_path(c)
            if t is not None:
                return t
        return None

    def other_repo_target(self, other: RepoInfo, rel: str | None) -> ResolvedTarget:
        if rel is not None:
            files = other.files()
            if rel in files:
                return ResolvedTarget(kind="file", id=file_id(other.name, rel))
            if any(f.startswith(rel + "/") for f in files):
                return ResolvedTarget(kind="dir", id=dir_id(other.name, rel))
        return ResolvedTarget(kind="repo", id=repo_id(other.name))


def _ts_paths(root: Path) -> dict[str, str]:
    """`compilerOptions.paths` from tsconfig/jsconfig, resolved against baseUrl."""
    import json
    import re

    for name in ("tsconfig.json", "jsconfig.json"):
        p = root / name
        if not p.exists():
            continue
        try:
            text = re.sub(r"//[^\n]*|/\*.*?\*/", "", p.read_text(errors="replace"), flags=re.S)
            text = re.sub(r",\s*([}\]])", r"\1", text)  # trailing commas
            data = json.loads(text)
        except (OSError, ValueError):
            continue
        opts = data.get("compilerOptions", {}) or {}
        base = (opts.get("baseUrl") or ".").strip("./") or ""
        out: dict[str, str] = {}
        for alias, targets in (opts.get("paths") or {}).items():
            if targets:
                target = str(targets[0]).lstrip("./")
                out[alias] = posixpath.normpath(posixpath.join(base, target)) if base else target
        return out
    return {}


def relative_candidates(from_file: str, target: str) -> str:
    """Join a path relative to the directory of `from_file`, normalised."""
    base = posixpath.dirname(from_file)
    return posixpath.normpath(posixpath.join(base, target)) if base else posixpath.normpath(target)


@dataclass
class Extraction:
    imports: list[Import] = field(default_factory=list)
    symbols: list[Symbol] | None = None  # None -> use the generic tags extraction
    references: list[Reference] | None = None


class LanguageHandler:
    languages: tuple[str, ...] = ()

    def extract(self, ctx: ScanContext, path: str, tree: Tree | None, data: bytes) -> Extraction:
        return Extraction()

    def resolve(self, ctx: ScanContext, path: str, imp: Import) -> ResolvedTarget | None:
        return None
