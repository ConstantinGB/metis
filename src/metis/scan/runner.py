"""Orchestrates a Local Scan of one repo checkout and persists local.json (+ git.json)."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from metis import config
from metis.model import (
    FileEntry,
    GitSignals,
    LocalScan,
    Project,
    Repo,
    ScanStats,
    Settings,
    file_id,
)
from metis.projects import git, registry
from metis.scan import docs, frameworks, gitsignals, roles, sqltext, walker
from metis.scan.imports import RepoInfo, ScanContext, get_handler
from metis.scan.linker import link
from metis.scan.parsers import NO_PARSE, language_supported, parse
from metis.scan.symbols import extract_symbols

Progress = Callable[[int, int, str], None]
TEXT_LANGS_FOR_FRAMEWORKS = {
    "python",
    "javascript",
    "typescript",
    "tsx",
    "go",
    "php",
    "ruby",
    "java",
    "kotlin",
    "csharp",
    "rust",
    "bash",
    "yaml",
}


def scan_path(
    root: Path,
    repo_name: str,
    branch: str,
    *,
    package_names: list[str] | None = None,
    go_module: str | None = None,
    other_repos: list[RepoInfo] | None = None,
    max_file_bytes: int = 1_000_000,
    commit: str | None = None,
    progress: Progress | None = None,
) -> LocalScan:
    t0 = time.time()
    files = walker.list_files(root)
    ctx = ScanContext.build(repo_name, root, files, package_names, go_module, other_repos)
    entries: list[FileEntry] = []
    languages: Counter[str] = Counter()
    unsupported: set[str] = set()
    parsed = 0

    for i, rel in enumerate(files):
        if progress:
            progress(i, len(files), rel)
        lang = walker.detect_language(rel)
        fe = FileEntry(
            id=file_id(repo_name, rel), path=rel, language=lang, is_doc=walker.is_doc(rel)
        )
        data, reason = walker.read_file(root, rel, max_file_bytes)
        if data is None:
            fe.skipped_reason = reason
            try:
                fe.size = (root / rel).stat().st_size
            except OSError:
                pass
            entries.append(fe)
            continue
        fe.size = len(data)
        fe.line_count = walker.count_lines(data)
        fe.content_hash = walker.content_hash(data)
        text = data.decode("utf-8", errors="replace")
        if lang:
            languages[lang] += 1
        handler = get_handler(lang)
        tree = parse(lang, data) if lang and language_supported(lang) else None
        if lang and tree is None and handler is None and lang not in NO_PARSE:
            unsupported.add(lang)
        symbols, refs = extract_symbols(repo_name, rel, lang, tree) if (tree and lang) else ([], [])
        if handler is not None:
            ex = handler.extract(ctx, rel, tree, data)
            fe.imports = sorted(ex.imports, key=lambda x: (x.line, x.module))
            if ex.symbols is not None:
                symbols = ex.symbols
            if ex.references is not None:
                refs = ex.references
        if tree is not None or handler is not None:
            parsed += 1

        # framework facts, env keys, SQL in code
        if lang in TEXT_LANGS_FOR_FRAMEWORKS:
            r_syms, r_refs = frameworks.extract_routes(repo_name, rel, lang, text)
            c_syms, c_refs = frameworks.extract_commands(repo_name, rel, lang, text)
            symbols = symbols + r_syms + c_syms
            refs = refs + r_refs + c_refs + frameworks.extract_env_reads(lang, text)
            if lang not in ("yaml", "bash"):
                refs = refs + sqltext.table_refs(text)
        refs = refs + frameworks.extract_env_defines(rel, lang, text)

        # author documentation and roles
        header, sym_docs = docs.extract_docs(lang, text, symbols)
        fe.header_doc = header
        for s in symbols:
            if s.id in sym_docs:
                s.docstring = sym_docs[s.id]
        role = roles.classify(rel, lang, text, symbols, fe.is_doc)
        fe.roles, fe.title_hint, fe.generated, fe.vendored = (
            role.roles,
            role.title_hint,
            role.generated,
            role.vendored,
        )

        fe.symbols = sorted(symbols, key=lambda s: (s.start_line, s.qualified_name))
        fe.references = sorted(refs, key=lambda r: (r.line, r.col, r.kind, r.name))
        entries.append(fe)

    entries.sort(key=lambda f: f.path)
    scan = LocalScan(repo=repo_name, branch=branch, commit=commit, root=str(root), files=entries)
    link(ctx, scan)
    scan.stats = ScanStats(
        files=len(entries),
        parsed_files=parsed,
        symbols=sum(len(f.symbols) for f in entries),
        edges=len(scan.edges),
        languages=dict(sorted(languages.items())),
        duration_s=round(time.time() - t0, 3),
        unsupported_languages=sorted(unsupported),
    )
    return scan


def other_repo_infos(project: Project, exclude: str) -> list[RepoInfo]:
    infos: list[RepoInfo] = []
    for r in project.repos:
        if r.name == exclude:
            continue
        root = r.checkouts.get(r.active_branch) or r.checkouts.get(r.default_branch)
        infos.append(
            RepoInfo(
                name=r.name,
                package_names=r.package_names,
                go_module=r.go_module,
                root=Path(root) if root else None,
            )
        )
    return infos


def scan_json_path(project_name: str, repo_name: str, branch: str) -> Path:
    return registry.scans_dir(project_name, repo_name, branch) / "local.json"


def git_json_path(project_name: str, repo_name: str, branch: str) -> Path:
    return registry.scans_dir(project_name, repo_name, branch) / "git.json"


def write_scan(scan: LocalScan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scan.model_dump_json(indent=2))


def load_scan(path: Path) -> LocalScan | None:
    if not path.exists():
        return None
    return LocalScan.model_validate_json(path.read_text())


def load_git_signals(path: Path) -> GitSignals | None:
    if not path.exists():
        return None
    try:
        return GitSignals.model_validate_json(path.read_text())
    except ValueError:
        return None


def load_repo_scan(project: Project, repo: Repo, branch: str | None = None) -> LocalScan | None:
    return load_scan(scan_json_path(project.name, repo.name, branch or repo.active_branch))


def scan_repo(
    project: Project,
    repo: Repo,
    branch: str | None = None,
    settings: Settings | None = None,
    progress: Progress | None = None,
) -> LocalScan:
    settings = settings or config.load_settings()
    branch = branch or repo.active_branch
    root = registry.checkout_path(project, repo, branch)
    commit = git.head_commit(root) if repo.is_git else None
    previous = load_scan(scan_json_path(project.name, repo.name, branch))
    scan = scan_path(
        root,
        repo.name,
        branch,
        package_names=repo.package_names,
        go_module=repo.go_module,
        other_repos=other_repo_infos(project, repo.name),
        max_file_bytes=settings.max_file_bytes,
        commit=commit,
        progress=progress,
    )
    if settings.use_language_servers:
        from metis.scan import lsp

        lsp.refine(
            root, scan, registry.scans_dir(project.name, repo.name, branch), progress=progress
        )
    write_scan(scan, scan_json_path(project.name, repo.name, branch))
    if repo.is_git:
        if progress:
            progress(scan.stats.files, scan.stats.files, "git history")
        signals = gitsignals.collect(
            root, repo.name, branch, known_files={f.path for f in scan.files}
        )
        git_json_path(project.name, repo.name, branch).write_text(signals.model_dump_json(indent=2))
    if previous is not None:
        from metis.ai import stale

        stale.mark_stale(project.name, repo.name, branch, previous, scan)
    return scan
