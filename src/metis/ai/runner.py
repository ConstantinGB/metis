"""AI Scan: templates (no model) -> packets (one cheap call per file) -> agent (unclear files, overview).
Also the diff explanation."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from metis import config
from metis import store as st
from metis.ai import costlog, packets, prompts
from metis.ai.templates import template_summary
from metis.ai.tools import (
    AiRunContext,
    MetisTools,
    allowed_tool_names,
    build_server,
    record_summary,
)
from metis.model import AiIndex, DiffExplanation, FileEntry, Settings, now
from metis.projects import git, registry
from metis.scan.parsers import NO_PARSE
from metis.scan.runner import load_repo_scan
from metis.store import get_store

MAX_ROUNDS = 4
MAX_DIFF_CHARS = 80_000
PACKET_CALL_CAP = 0.30
PACKET_MAX_TURNS = (
    8  # structured output takes extra round-trips inside the CLI, more without thinking
)
SKIP_LANGUAGES = {"json", "toml", "ini", "text", "xml", "css", "scss", "html"}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "poetry.lock", "uv.lock", "Cargo.lock", "go.sum"}
DISALLOWED = ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "WebFetch", "WebSearch", "Task"]


class AiUnavailable(RuntimeError):
    pass


def check_available() -> tuple[bool, str]:
    cli = shutil.which("claude")
    if not cli:
        return False, "the `claude` command (Claude Code) is not installed or not on PATH"
    try:
        out = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=15)
        if out.returncode == 0:
            data = json.loads(out.stdout or "{}")
            if data.get("loggedIn"):
                return True, f"Claude Code logged in via {data.get('authMethod', 'unknown')}"
            return False, "Claude Code is installed but not logged in; run `claude login`"
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        pass
    return True, "Claude Code found (login status unknown)"


def wants_summary(f: FileEntry) -> bool:
    if f.skipped_reason or not f.language or f.language in SKIP_LANGUAGES:
        if not (f.path.rsplit("/", 1)[-1].startswith(".env") and not f.skipped_reason):
            return False
    if f.path.rsplit("/", 1)[-1] in SKIP_NAMES:
        return f.generated  # lockfiles get a template summary, nothing more
    if f.language in NO_PARSE and f.language not in (
        "markdown",
        "yaml",
        "sql",
        "dockerfile",
        "make",
    ):
        return False
    return True


def _install_skills(project_name: str) -> None:
    dest = registry.project_dir(project_name) / ".claude" / "skills"
    dest.mkdir(parents=True, exist_ok=True)
    for src in prompts.SKILLS_DIR.iterdir():
        if src.is_dir():
            shutil.copytree(src, dest / src.name, dirs_exist_ok=True)


def _structured(result) -> dict | None:
    payload = getattr(result, "structured_output", None)
    if isinstance(payload, dict):
        return payload
    text = getattr(result, "result", None)
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


# --------------------------------------------------------------------------- stages


def run_templates(ctx: AiRunContext) -> int:
    n = 0
    for path, fe in list(ctx.pending.items()):
        text = ctx.store.read_source(ctx.repo_name, path)
        summary = template_summary(fe, ctx.scan, text)
        if summary is not None:
            record_summary(ctx, summary)
            n += 1
    return n


async def run_packets(ctx: AiRunContext, settings: Settings) -> None:
    """One no-tool call per pending file, leaves first, several at a time. Unclear files stay pending."""
    from claude_agent_sdk import ClaudeAgentOptions, ResultError, ResultMessage, query

    system = prompts.packet_system_prompt(ctx.repo_name, ctx.branch)
    sem = asyncio.Semaphore(max(1, settings.packet_concurrency))
    failures: dict[str, int] = {}
    cwd = str(registry.project_dir(ctx.project_name))

    async def one(path: str) -> None:
        async with sem:
            fe = ctx.pending.get(path)
            if fe is None or ctx.remaining() < 0.02:
                return
            text = ctx.store.read_source(ctx.repo_name, path) or ""
            packet = packets.build_packet(ctx.store, ctx.scan, fe, text)
            options = ClaudeAgentOptions(
                cwd=cwd,
                system_prompt=system,
                model=settings.packet_model,
                permission_mode="dontAsk",
                allowed_tools=[],
                disallowed_tools=DISALLOWED,
                max_turns=PACKET_MAX_TURNS,
                max_budget_usd=round(min(PACKET_CALL_CAP, ctx.remaining()), 2),
                output_format={"type": "json_schema", "schema": packets.PACKET_SCHEMA},
                thinking=({"type": "disabled"} if "haiku" in settings.packet_model else None),
                setting_sources=[],
            )
            result = None
            failed: str | None = None
            try:
                async for message in query(prompt=packet, options=options):
                    if isinstance(message, ResultMessage):
                        result = message
            except ResultError as e:
                # the ResultMessage was yielded before the error; keep its payload if usable
                if result is None or not _structured(result):
                    failed = (
                        getattr(e, "subtype", None)
                        or getattr(e, "terminal_reason", None)
                        or "ResultError"
                    )
            except Exception as e:  # noqa: BLE001 - one failed packet must not stop the run
                failed = type(e).__name__
            if result is not None:
                cost = result.total_cost_usd or 0.0
                ctx.add_cost(cost, "packet")
                costlog.log_call(
                    ctx.project_name,
                    ctx.repo_name,
                    ctx.branch,
                    "packet",
                    settings.packet_model,
                    path,
                    cost,
                    getattr(result, "usage", None),
                    note=failed,
                )
            if failed:
                failures[path] = failures.get(path, 0) + 1
                ctx.progress(f"packet failed for {path}: {failed}")
                return
            data = _structured(result) if result else None
            if not data:
                failures[path] = failures.get(path, 0) + 1
                ctx.progress(f"packet gave no usable answer for {path}")
                return
            if data.get("unclear"):
                ctx.unclear[path] = str(data.get("reason") or "")[:300]
                ctx.progress(f"unclear: {path}")
                return
            merged = packets.merge(
                packets.draft_summary(fe), data.get("summary"), bool(data.get("keep"))
            )
            try:
                summary = packets.summary_from(fe, merged, settings.packet_model, "packet")
            except ValueError as e:
                failures[path] = failures.get(path, 0) + 1
                ctx.progress(f"packet summary rejected for {path}: {str(e)[:80]}")
                return
            record_summary(ctx, summary)

    for wave in packets.waves(ctx.scan, sorted(ctx.pending)):
        if ctx.remaining() < 0.02:
            ctx.progress("budget cap reached during packets")
            break
        await asyncio.gather(*(one(p) for p in wave if p in ctx.pending and failures.get(p, 0) < 2))
    for path, n in failures.items():
        if n >= 2 and path in ctx.pending:
            ctx.unclear.setdefault(path, "packet call failed twice")


async def run_agent(ctx: AiRunContext, settings: Settings, root: Path) -> None:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    server = build_server(MetisTools(ctx))
    system = prompts.scan_system_prompt(ctx.repo_name, ctx.branch, root)
    for round_no in range(MAX_ROUNDS):
        remaining = ctx.remaining()
        if remaining <= 0.05:
            ctx.progress(f"budget cap of ${ctx.budget:.2f} reached")
            break
        options = ClaudeAgentOptions(
            cwd=str(registry.project_dir(ctx.project_name)),
            system_prompt=system,
            model=settings.model,
            permission_mode="dontAsk",
            allowed_tools=["Read", "Grep", "Glob", *allowed_tool_names()],
            disallowed_tools=DISALLOWED,
            mcp_servers={"metis": server},
            max_budget_usd=round(remaining, 2),
            max_turns=settings.max_ai_turns,
            setting_sources=[],
        )
        unclear = "\n".join(f"- {p}: {why}" for p, why in sorted(ctx.unclear.items())) or "(none)"
        if round_no == 0:
            prompt = (
                f"{len(ctx.pending)} files still need a summary. The packet pass flagged these as unclear:\n"
                f"{unclear}\n\n{prompts.scan_user_prompt()}"
            )
        else:
            prompt = (
                f"Continue. {len(ctx.pending)} files are still pending; call metis_list_files and keep going "
                "until none remain, then submit the repository overview if you have not yet."
            )
        result = None
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                result = message
        cost = (result.total_cost_usd or 0.0) if result else 0.0
        ctx.add_cost(cost, "agent")
        costlog.log_call(
            ctx.project_name,
            ctx.repo_name,
            ctx.branch,
            "agent",
            settings.model,
            None,
            cost,
            getattr(result, "usage", None),
            note=f"round {round_no + 1}",
        )
        ctx.index.runs += 1
        st.save_ai_index(ctx.project_name, ctx.repo_name, ctx.branch, ctx.index)
        overview_done = st.load_ai_overview(ctx.project_name, ctx.repo_name, ctx.branch) is not None
        if not ctx.pending and overview_done:
            break
        if result is not None and result.is_error and result.errors:
            ctx.progress(f"session ended: {'; '.join(result.errors)[:200]}")
        if result is not None and result.subtype == "error_max_budget_usd":
            break


# --------------------------------------------------------------------------- entry points


async def run_ai_scan(
    project_name: str,
    repo_name: str,
    branch: str | None = None,
    *,
    force: bool = False,
    on_progress: Callable[[str, float], None] | None = None,
    settings: Settings | None = None,
) -> AiIndex:
    settings = settings or config.load_settings()
    project = registry.load_project(project_name)
    repo = project.repo(repo_name)
    branch = branch or repo.active_branch
    scan = load_repo_scan(project, repo, branch)
    if scan is None:
        raise AiUnavailable("run a Local Scan first")
    ok, why = check_available()
    if not ok:
        raise AiUnavailable(why)

    index = st.load_ai_index(project_name, repo_name, branch)
    pending: dict[str, FileEntry] = {}
    for f in scan.files:
        if not wants_summary(f):
            continue
        existing = st.load_ai_summary(project_name, repo_name, branch, f.path)
        if (
            force
            or index.hashes.get(f.path) != f.content_hash
            or (existing is not None and existing.stale)
        ):
            pending[f.path] = f
    store = get_store(project_name)
    root = Path(repo.checkouts[branch])
    ctx = AiRunContext(
        project_name=project_name,
        repo_name=repo_name,
        branch=branch,
        root=root,
        scan=scan,
        store=store,
        index=index,
        pending=pending,
        model=settings.model,
        on_progress=on_progress,
        total=len(pending),
        budget=settings.budget_usd_per_run,
    )
    index.model = settings.model
    overview_done = st.load_ai_overview(project_name, repo_name, branch) is not None
    if not pending and overview_done:
        ctx.progress("everything is already summarised")
        if on_progress:
            on_progress("nothing to do", 1.0)
        return index

    _install_skills(project_name)
    ctx.progress(f"starting: {len(pending)} files to explain, budget ${ctx.budget:.2f}")
    n_templates = run_templates(ctx)
    if n_templates:
        ctx.progress(f"{n_templates} files summarised from templates (no model)")
    if ctx.pending and settings.packet_model:
        await run_packets(ctx, settings)
    if ctx.pending or not overview_done:
        ctx.progress(
            f"agent stage: {len(ctx.pending)} files" + (", overview" if not overview_done else "")
        )
        await run_agent(ctx, settings, root)
    index.last_run = now()
    st.save_ai_index(project_name, repo_name, branch, index)
    if on_progress:
        on_progress(f"finished: {ctx.submitted}/{ctx.total} files, ${ctx.spent:.2f} this run", 1.0)
    return index


async def explain_diff(
    project_name: str, repo_name: str, base: str, head: str, settings: Settings | None = None
) -> DiffExplanation:
    settings = settings or config.load_settings()
    ok, why = check_available()
    if not ok:
        raise AiUnavailable(why)
    project = registry.load_project(project_name)
    repo = project.repo(repo_name)
    root = Path(repo.checkouts[repo.default_branch])
    git.fetch(root, token=config.get_token(project_name, repo_name))
    numstat = git.diff_numstat(root, base, head)
    commits = git.log_between(root, base, head)
    diff_text = git.diff_full(root, base, head)
    truncated = len(diff_text) > MAX_DIFF_CHARS
    if truncated:
        diff_text = diff_text[:MAX_DIFF_CHARS]
    summaries: dict[str, str] = {}
    for _a, _d, path in numstat[:60]:
        for b in (head, base, repo.active_branch):
            s = st.load_ai_summary(project_name, repo_name, b, path)
            if s:
                summaries[path] = f"{s.title}: {s.purpose}"
                break
    checkout = Path(repo.checkouts.get(head) or repo.checkouts[repo.default_branch])

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    options = ClaudeAgentOptions(
        cwd=str(checkout),
        system_prompt=prompts.diff_system_prompt(repo_name, checkout),
        model=settings.model,
        permission_mode="dontAsk",
        allowed_tools=["Read", "Grep", "Glob"],
        disallowed_tools=DISALLOWED,
        max_budget_usd=settings.budget_usd_per_run,
        max_turns=25,
        setting_sources=[],
    )
    text_parts: list[str] = []
    cost = 0.0
    async for message in query(
        prompt=prompts.diff_user_prompt(
            base, head, commits, numstat, summaries, diff_text, truncated
        ),
        options=options,
    ):
        if isinstance(message, AssistantMessage):
            text_parts = [b.text for b in message.content if isinstance(b, TextBlock)] or text_parts
        elif isinstance(message, ResultMessage):
            cost = message.total_cost_usd or 0.0
            if message.result:
                text_parts = [message.result]
    expl = DiffExplanation(
        repo=repo_name,
        base=base,
        head=head,
        explanation="\n\n".join(text_parts).strip() or "(no explanation returned)",
        cost_usd=cost,
        model=settings.model,
    )
    st.save_diff_explanation(project_name, repo_name, expl)
    return expl
