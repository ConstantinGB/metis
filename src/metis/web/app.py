"""FastAPI application: project pages, column browser, diff view, settings, jobs."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from metis import __version__, config
from metis.ai import runner as ai_runner
from metis.ai import search as ai_search
from metis.model import Settings
from metis.projects import git, registry
from metis.render.markdown import (
    ai_search_card,
    node_title,
    render_inline,
    render_node,
    search_card,
    to_html,
)
from metis.render.source import render_node_source
from metis.scan import runner as scan_runner
from metis.store import get_store, load_diff_explanation
from metis.web.jobs import Job, JobManager
from metis.web.tree import render_tree

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters["md"] = to_html
templates.env.filters["mdi"] = render_inline
templates.env.globals["version"] = __version__
templates.env.globals["quote"] = lambda s: quote(s, safe="")
templates.env.globals["current_theme"] = lambda: config.load_settings().theme


def create_app() -> FastAPI:
    app = FastAPI(title="METIS")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.state.jobs = JobManager()
    _register(app)
    return app


def _jobs(request: Request) -> JobManager:
    return request.app.state.jobs


def _project_or_404(name: str):
    try:
        return registry.load_project(name)
    except registry.RegistryError as e:
        raise HTTPException(404, str(e)) from e


MAX_TABS = 12


def _parse_tabs(tabs: str, path: str, active: int) -> tuple[list[list[str]], int]:
    if tabs:
        rows = [[unquote(x) for x in t.split(",") if x] for t in tabs.split("|")]
    elif path:
        rows = [[p for p in path.split(",") if p]]
    else:
        rows = [[]]
    rows = rows[:MAX_TABS] or [[]]
    return rows, min(max(active, 0), len(rows) - 1)


def _render_row(store, ids: list[str]) -> list[dict]:
    cards = []
    for col, node_id in enumerate(ids, start=1):
        try:
            card = render_node(store, node_id)
        except ValueError:
            continue
        cards.append({"col": col, "card": card, "html": to_html(card.markdown)})
    return cards


def _card_response(request: Request, name: str, card, col: int):
    return templates.TemplateResponse(
        request,
        "card.html",
        {"col": col, "card": card, "html": to_html(card.markdown), "pname": name},
    )


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _register(app: FastAPI) -> None:  # noqa: C901 - one place for all routes keeps the app readable
    # ------------------------------------------------------------------ home / settings

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        ok, why = ai_runner.check_available()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "projects": registry.list_projects(),
                "ai_ok": ok,
                "ai_why": why,
            },
        )

    @app.post("/projects")
    def create_project(name: str = Form(...)):
        try:
            project = registry.create_project(name)
        except registry.RegistryError as e:
            raise HTTPException(400, str(e)) from e
        return _redirect(f"/p/{project.name}")

    @app.post("/p/{name}/delete")
    def delete_project(name: str):
        _project_or_404(name)
        registry.delete_project(name)
        return _redirect("/")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, saved: int = 0):
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "settings": config.load_settings(),
                "saved": saved,
                "home": config.metis_home(),
            },
        )

    @app.post("/settings")
    def settings_save(
        model: str = Form(...),
        budget_usd_per_run: float = Form(...),
        max_ai_turns: int = Form(...),
        max_file_bytes: int = Form(...),
        port: int = Form(...),
        theme: str = Form("dark"),
        packet_model: str = Form("claude-haiku-4-5"),
        packet_concurrency: int = Form(4),
        use_language_servers: str = Form(""),
    ):
        config.save_settings(
            Settings(
                model=model.strip(),
                budget_usd_per_run=budget_usd_per_run,
                max_ai_turns=max_ai_turns,
                max_file_bytes=max_file_bytes,
                port=port,
                theme="light" if theme == "light" else "dark",
                packet_model=packet_model.strip() or "claude-haiku-4-5",
                packet_concurrency=max(1, min(packet_concurrency, 16)),
                use_language_servers=bool(use_language_servers),
            )
        )
        return _redirect("/settings?saved=1")

    # ------------------------------------------------------------------ project page

    @app.get("/p/{name}", response_class=HTMLResponse)
    def project_page(request: Request, name: str, error: str = ""):
        project = _project_or_404(name)
        store = get_store(name)
        rows = []
        for repo in project.repos:
            scan = store.scan(repo.name)
            index = store.ai_index(repo.name)
            rows.append(
                {
                    "repo": repo,
                    "scan": scan,
                    "index": index,
                    "ai_done": len(index.hashes),
                    "ai_total": len([f for f in scan.files if ai_runner.wants_summary(f)])
                    if scan
                    else 0,
                    "overview": store.ai_overview(repo.name),
                }
            )
        ok, why = ai_runner.check_available()
        return templates.TemplateResponse(
            request,
            "project.html",
            {
                "project": project,
                "rows": rows,
                "jobs": _jobs(request).for_project(name),
                "active": _jobs(request).any_active(name),
                "ai_ok": ok,
                "ai_why": why,
                "settings": config.load_settings(),
                "error": error,
                "search_cost": ai_search.total_search_cost(name),
            },
        )

    @app.get("/p/{name}/jobs", response_class=HTMLResponse)
    def jobs_partial(request: Request, name: str):
        return templates.TemplateResponse(
            request,
            "jobs.html",
            {
                "jobs": _jobs(request).for_project(name),
                "active": _jobs(request).any_active(name),
            },
        )

    @app.post("/p/{name}/repos")
    async def add_repo(request: Request, name: str, source: str = Form(...), token: str = Form("")):
        _project_or_404(name)
        source = source.strip()

        def work(job: Job) -> None:
            project = registry.load_project(name)
            job.message = f"adding {source}"
            repo = registry.add_repo(project, source, token=token.strip() or None)
            job.repo = repo.name
            job.message = "scanning"
            scan_runner.scan_repo(project, repo, progress=job.set_progress)

        _jobs(request).start("add repo", name, work)
        return _redirect(f"/p/{name}")

    @app.post("/p/{name}/repos/{repo_name}/remove")
    def remove_repo(name: str, repo_name: str):
        project = _project_or_404(name)
        try:
            registry.remove_repo(project, repo_name)
        except KeyError:
            raise HTTPException(404, "no such repo") from None
        return _redirect(f"/p/{name}")

    @app.post("/p/{name}/repos/{repo_name}/branch")
    async def set_branch(request: Request, name: str, repo_name: str, branch: str = Form(...)):
        project = _project_or_404(name)
        repo = project.repo(repo_name)

        def work(job: Job) -> None:
            proj = registry.load_project(name)
            r = proj.repo(repo_name)
            job.message = f"checking out {branch}"
            registry.set_active_branch(proj, r, branch)
            if scan_runner.load_repo_scan(proj, r, branch) is None:
                job.message = "scanning"
                scan_runner.scan_repo(proj, r, branch, progress=job.set_progress)

        _jobs(request).start("switch branch", name, work, repo=repo.name, branch=branch)
        return _redirect(f"/p/{name}")

    @app.post("/p/{name}/repos/{repo_name}/refresh")
    def refresh_branches(name: str, repo_name: str):
        project = _project_or_404(name)
        try:
            registry.refresh_branches(project, project.repo(repo_name))
        except git.GitError as e:
            return _redirect(f"/p/{name}?error={quote(str(e))}")
        return _redirect(f"/p/{name}")

    @app.post("/p/{name}/scan")
    async def local_scan(request: Request, name: str, repo_name: str = Form("")):
        project = _project_or_404(name)
        targets = [r for r in project.repos if not repo_name or r.name == repo_name]

        def work(job: Job) -> None:
            proj = registry.load_project(name)
            for r in targets:
                repo = proj.repo(r.name)
                job.repo = repo.name
                job.branch = repo.active_branch
                scan_runner.scan_repo(proj, repo, progress=job.set_progress)

        _jobs(request).start("local scan", name, work, repo=repo_name or None)
        return _redirect(f"/p/{name}")

    @app.post("/p/{name}/ai-scan")
    async def ai_scan(
        request: Request, name: str, repo_name: str = Form(...), force: str = Form("")
    ):
        project = _project_or_404(name)
        repo = project.repo(repo_name)
        if _jobs(request).active_for(name, "ai scan", repo.name):
            return _redirect(f"/p/{name}?error=AI+scan+already+running")

        async def work(job: Job) -> None:
            def on_progress(message: str, fraction: float) -> None:
                job.message = message
                job.progress = fraction

            index = await ai_runner.run_ai_scan(
                name,
                repo.name,
                force=bool(force),
                on_progress=on_progress,
            )
            job.result = {"cost_usd": index.total_cost_usd}
            job.message = f"done, total cost so far ${index.total_cost_usd:.2f}"

        _jobs(request).start("ai scan", name, work, repo=repo.name, branch=repo.active_branch)
        return _redirect(f"/p/{name}")

    # ------------------------------------------------------------------ browse

    @app.get("/p/{name}/browse", response_class=HTMLResponse)
    def browse(request: Request, name: str, tabs: str = "", path: str = "", active: int = 0):
        _project_or_404(name)
        store = get_store(name)
        rows, active = _parse_tabs(tabs, path, active)
        cards = _render_row(store, rows[active])
        state = {
            "tabs": [
                {"ids": r, "title": node_title(store, r[-1]) if r else "New tab"} for r in rows
            ],
            "active": active,
        }
        ok, _why = ai_runner.check_available()
        return templates.TemplateResponse(
            request,
            "browse.html",
            {
                "project": store.project,
                "tree": render_tree(store),
                "cards": cards,
                "state": json.dumps(state),
                "ai_ok": ok,
                "pname": name,
            },
        )

    @app.get("/p/{name}/row", response_class=HTMLResponse)
    def row_partial(request: Request, name: str, path: str = ""):
        _project_or_404(name)
        store = get_store(name)
        ids = [unquote(p) for p in path.split(",") if p]
        return templates.TemplateResponse(
            request, "row.html", {"cards": _render_row(store, ids), "pname": name}
        )

    @app.get("/p/{name}/card", response_class=HTMLResponse)
    def card_partial(request: Request, name: str, node: str, col: int = 1):
        _project_or_404(name)
        store = get_store(name)
        try:
            card = render_node(store, node)
        except ValueError as e:
            raise HTTPException(400, f"bad node id: {e}") from e
        return _card_response(request, name, card, col)

    @app.get("/p/{name}/source", response_class=HTMLResponse)
    def source_partial(name: str, node: str):
        _project_or_404(name)
        try:
            rendered = render_node_source(get_store(name), node)
        except ValueError as e:
            raise HTTPException(400, f"bad node id: {e}") from e
        if rendered is None:
            return HTMLResponse('<p class="muted small">Source is not available for this node.</p>')
        return HTMLResponse(rendered[0])

    @app.get("/p/{name}/search-card", response_class=HTMLResponse)
    def search_card_partial(request: Request, name: str, q: str = "", col: int = 1):
        _project_or_404(name)
        return _card_response(request, name, search_card(get_store(name), q), col)

    @app.post("/p/{name}/ai-search", response_class=HTMLResponse)
    async def ai_search_start(request: Request, name: str, q: str = Form(...), col: int = Form(1)):
        _project_or_404(name)
        store = get_store(name)
        try:
            result = await ai_search.search_summaries(name, q)
        except ai_runner.AiUnavailable as e:
            raise HTTPException(503, str(e)) from e
        return _card_response(request, name, ai_search_card(store, q, result, result.stage), col)

    @app.post("/p/{name}/ai-search/deeper")
    async def ai_search_deeper(request: Request, name: str, q: str = Form(...)):
        _project_or_404(name)
        if not _jobs(request).active_for(name, "ai search"):

            async def work(job: Job) -> None:
                job.message = q
                result = await ai_search.search_code(name, q)
                job.result = {"cost_usd": result.cost_usd}

            _jobs(request).start("ai search", name, work)
        return JSONResponse({"ok": True})

    @app.get("/p/{name}/ai-search/card", response_class=HTMLResponse)
    def ai_search_card_partial(request: Request, name: str, q: str, col: int = 1):
        _project_or_404(name)
        store = get_store(name)
        result = ai_search.latest_result(name, q)
        stage = result.stage if result else "pending"
        if result and result.stage == "summaries" and _jobs(request).active_for(name, "ai search"):
            stage = "pending"
        card = ai_search_card(store, q, result, stage)
        if stage == "pending":
            card.stage = "pending"
        return _card_response(request, name, card, col)

    # ------------------------------------------------------------------ diff

    @app.get("/p/{name}/diff", response_class=HTMLResponse)
    def diff_page(request: Request, name: str, repo_name: str = "", base: str = "", head: str = ""):
        project = _project_or_404(name)
        repos = [r for r in project.repos if r.is_git]
        repo = next((r for r in repos if r.name == repo_name), repos[0] if repos else None)
        ctx: dict = {
            "project": project,
            "repos": repos,
            "repo": repo,
            "base": base,
            "head": head,
            "files": [],
            "commits": [],
            "explanation": None,
            "diffs": {},
            "active": _jobs(request).any_active(name),
        }
        if repo and base and head:
            root = Path(repo.checkouts[repo.default_branch])
            git.fetch(root, token=config.get_token(name, repo.name))
            ctx["files"] = git.diff_numstat(root, base, head)
            ctx["commits"] = git.log_between(root, base, head)
            ctx["diffs"] = {
                f: git.diff_file(root, base, head, f) for _a, _d, f in ctx["files"][:80]
            }
            ctx["explanation"] = load_diff_explanation(name, repo.name, base, head)
            ctx["explain_job"] = _jobs(request).active_for(name, "explain diff", repo.name)
        return templates.TemplateResponse(request, "diff.html", ctx)

    @app.post("/p/{name}/diff/explain")
    async def explain_diff(
        request: Request,
        name: str,
        repo_name: str = Form(...),
        base: str = Form(...),
        head: str = Form(...),
    ):
        _project_or_404(name)

        async def work(job: Job) -> None:
            job.message = "asking Claude to explain the changes"
            expl = await ai_runner.explain_diff(name, repo_name, base, head)
            job.result = {"cost_usd": expl.cost_usd}

        _jobs(request).start("explain diff", name, work, repo=repo_name, branch=f"{base}..{head}")
        return _redirect(
            f"/p/{name}/diff?repo_name={repo_name}&base={quote(base)}&head={quote(head)}"
        )
