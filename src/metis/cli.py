"""Command line entry point: `metis serve` plus headless project/scan commands."""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import webbrowser

from metis import __version__, config


def _print_progress(done: int, total: int, message: str) -> None:
    sys.stderr.write(f"\r  [{done}/{total}] {message[:70]:<70}")
    sys.stderr.flush()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from metis.web.app import create_app

    port = args.port or config.load_settings().port
    url = f"http://{args.host}:{port}/"
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    print(f"METIS {__version__} at {url}  (data in {config.metis_home()})")
    uvicorn.run(create_app(), host=args.host, port=port, log_level="warning")
    return 0


def cmd_projects(args: argparse.Namespace) -> int:
    from metis.projects import registry

    for p in registry.list_projects():
        print(
            f"{p.name}: {', '.join(f'{r.name}@{r.active_branch}' for r in p.repos) or '(no repos)'}"
        )
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    from metis.projects import registry

    project = registry.create_project(args.name)
    print(f"created project {project.name} in {registry.project_dir(project.name)}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    from metis.projects import registry
    from metis.scan import runner

    project = registry.load_project(args.project)
    repo = registry.add_repo(project, args.source, token=args.token)
    print(f"added {repo.name} ({repo.source.describe()}) branches: {', '.join(repo.branches)}")
    scan = runner.scan_repo(project, repo, progress=_print_progress)
    print(
        f"\nscanned {scan.stats.files} files, {scan.stats.symbols} symbols, {scan.stats.edges} edges"
    )
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from metis.projects import registry
    from metis.scan import runner

    project = registry.load_project(args.project)
    for repo in project.repos:
        if args.repo and repo.name != args.repo:
            continue
        scan = runner.scan_repo(project, repo, args.branch, progress=_print_progress)
        print(
            f"\n{repo.name}@{scan.branch}: {scan.stats.files} files, {scan.stats.symbols} symbols, "
            f"{scan.stats.edges} edges in {scan.stats.duration_s}s"
        )
    return 0


def cmd_ai_scan(args: argparse.Namespace) -> int:
    from metis.ai import runner

    ok, why = runner.check_available()
    if not ok:
        print(f"AI scan unavailable: {why}", file=sys.stderr)
        return 2

    def on_progress(message: str, fraction: float) -> None:
        sys.stderr.write(f"\r  [{fraction * 100:5.1f}%] {message[:70]:<70}")
        sys.stderr.flush()

    index = asyncio.run(
        runner.run_ai_scan(
            args.project, args.repo, args.branch, force=args.force, on_progress=on_progress
        )
    )
    by_source = ", ".join(f"{v} {k}" for k, v in sorted(index.counts.items())) or "none"
    print(
        f"\nsummarised {len(index.hashes)} files ({by_source}); total cost so far ${index.total_cost_usd:.2f}"
    )
    return 0


def cmd_explain_diff(args: argparse.Namespace) -> int:
    from metis.ai import runner

    expl = asyncio.run(runner.explain_diff(args.project, args.repo, args.base, args.head))
    print(expl.explanation)
    print(f"\n(cost ${expl.cost_usd:.2f})", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="metis", description="Catalogue and visualise a codebase.")
    p.add_argument("--version", action="version", version=f"metis {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start the web GUI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_serve)

    sub.add_parser("projects", help="list projects").set_defaults(fn=cmd_projects)

    s = sub.add_parser("create", help="create a project")
    s.add_argument("name")
    s.set_defaults(fn=cmd_create)

    s = sub.add_parser("add", help="add a GitHub URL or local folder to a project and scan it")
    s.add_argument("project")
    s.add_argument("source")
    s.add_argument("--token", default=None, help="GitHub token for private repos")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("scan", help="run the Local Scan")
    s.add_argument("project")
    s.add_argument("--repo", default=None)
    s.add_argument("--branch", default=None)
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("ai-scan", help="run the AI Scan (needs Claude Code)")
    s.add_argument("project")
    s.add_argument("repo")
    s.add_argument("--branch", default=None)
    s.add_argument("--force", action="store_true", help="re-summarise every file")
    s.set_defaults(fn=cmd_ai_scan)

    s = sub.add_parser("explain-diff", help="explain the changes between two branches")
    s.add_argument("project")
    s.add_argument("repo")
    s.add_argument("base")
    s.add_argument("head")
    s.set_defaults(fn=cmd_explain_diff)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 - CLI surface
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
