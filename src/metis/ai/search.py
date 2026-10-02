"""AI search: stage 1 over names and summaries in one call, stage 2 as a read-only agent."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from metis import config
from metis.ai import prompts
from metis.ai.runner import AiUnavailable, check_available
from metis.model import AiSearchResult, SearchHit, Settings, now
from metis.projects import registry
from metis.store import ProjectStore, get_store

MAX_INDEX_CHARS = 120_000

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "why": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["node_id", "why"],
            },
        },
        "needs_code": {"type": "boolean"},
    },
    "required": ["results", "needs_code"],
}


# --------------------------------------------------------------------------- cache


def search_dir(project_name: str) -> Path:
    d = registry.project_dir(project_name) / "scans" / "_search"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_path(project_name: str, question: str, stage: str) -> Path:
    h = hashlib.sha1(question.strip().lower().encode()).hexdigest()[:12]
    return search_dir(project_name) / f"{h}-{stage}.json"


def scan_key(store: ProjectStore) -> str:
    return "|".join(f"{s.repo}@{s.branch}@{s.scanned_at.isoformat()}" for s in store.all_scans())


def load_result(project_name: str, question: str, stage: str) -> AiSearchResult | None:
    p = cache_path(project_name, question, stage)
    if not p.exists():
        return None
    try:
        r = AiSearchResult.model_validate_json(p.read_text())
    except ValueError:
        return None
    if r.scan_key != scan_key(get_store(project_name)):
        return None
    return r


def latest_result(project_name: str, question: str) -> AiSearchResult | None:
    return load_result(project_name, question, "code") or load_result(
        project_name, question, "summaries"
    )


def save_result(project_name: str, result: AiSearchResult) -> None:
    cache_path(project_name, result.question, result.stage).write_text(
        result.model_dump_json(indent=2)
    )


def total_search_cost(project_name: str) -> float:
    total = 0.0
    d = registry.project_dir(project_name) / "scans" / "_search"
    if not d.exists():
        return 0.0
    for p in d.glob("*.json"):
        try:
            total += float(json.loads(p.read_text()).get("cost_usd", 0.0))
        except (ValueError, OSError):
            pass
    return total


# --------------------------------------------------------------------------- index


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 2}


def build_index(store: ProjectStore, question: str, max_chars: int = MAX_INDEX_CHARS) -> str:
    qwords = _words(question)
    blocks: list[tuple[int, str]] = []  # (priority, text); lower priority first
    header = [
        "Node id formats: f:<repo>:<path> (file), s:<repo>:<path>#<qualified> (symbol), d:<repo>:<dir> (folder).",
    ]
    for repo in store.project.repos:
        scan = store.scan(repo.name)
        if scan is None:
            continue
        ov = store.ai_overview(repo.name)
        header.append(f"\n# repo {repo.name}" + (f": {ov.title}. {ov.purpose}" if ov else ""))
        for f in scan.files:
            if f.skipped_reason:
                continue
            ai = store.ai_summary(repo.name, f.path)
            syms = [s.qualified_name for s in f.symbols if s.parent is None][:40]
            line = f"f:{repo.name}:{f.path}"
            if ai:
                line += f" | {ai.title}: {ai.purpose}"
            if syms:
                line += f" | symbols: {', '.join(syms)}"
            if ai and ai.functions:
                for fn in ai.functions[:25]:
                    line += f"\n    {fn.symbol_id}: {fn.explanation}"
            relevance = len(qwords & _words(line))
            blocks.append((-relevance, line))
    blocks.sort(key=lambda b: b[0])
    out = "\n".join(header) + "\n"
    dropped = 0
    for _prio, line in blocks:
        if len(out) + len(line) + 1 > max_chars:
            dropped += 1
            continue
        out += line + "\n"
    if dropped:
        out += f"\n({dropped} less relevant files omitted from this index)\n"
    return out


def validate_hits(store: ProjectStore, hits: list[dict]) -> list[SearchHit]:
    out: list[SearchHit] = []
    seen: set[str] = set()
    for h in hits:
        node = str(h.get("node_id", "")).strip()
        if not node:
            continue
        if node.startswith(("f:", "s:", "d:", "r:")):
            ok = _node_exists(store, node)
        else:
            resolved = None
            for repo in store.project.repos:
                resolved = store.resolve_loose(repo.name, node)
                if resolved:
                    break
            node, ok = (resolved or node), bool(resolved)
        if not ok or node in seen:
            continue
        seen.add(node)
        conf = h.get("confidence") if h.get("confidence") in ("high", "medium", "low") else "medium"
        out.append(SearchHit(node_id=node, why=str(h.get("why", ""))[:300], confidence=conf))
    return out


def _node_exists(store: ProjectStore, node: str) -> bool:
    if node.startswith("r:"):
        return store.repo(node[2:]) is not None
    if node.startswith("d:"):
        repo, _, path = node[2:].partition(":")
        subdirs, files = store.dir_entries(repo, path)
        return bool(subdirs or files)
    if node.startswith("s:"):
        return store.symbol(node) is not None
    return store.file(node) is not None


# --------------------------------------------------------------------------- stages


def _system(stage: str) -> str:
    base = (
        "You are METIS search. A novice programmer asks where something lives in a codebase. "
        "Answer only with the JSON object described by the output schema.\n\n"
        + prompts.skill_text("metis-search")
    )
    if stage == "code":
        base += (
            "\n\nYou may read the code with Read, Grep and Glob (absolute paths are given). "
            "Confirm or replace the preliminary results by looking at the code. Never modify files."
        )
    return base


async def _run(
    project_name: str,
    question: str,
    stage: str,
    prompt: str,
    settings: Settings,
    tools: list[str],
    max_turns: int,
    add_dirs: list[str],
) -> AiSearchResult:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    options = ClaudeAgentOptions(
        cwd=str(registry.project_dir(project_name)),
        system_prompt=_system(stage),
        model=settings.model,
        permission_mode="dontAsk",
        allowed_tools=tools,
        disallowed_tools=[
            "Write",
            "Edit",
            "MultiEdit",
            "NotebookEdit",
            "Bash",
            "WebFetch",
            "WebSearch",
            "Task",
        ],
        add_dirs=add_dirs,
        max_turns=max_turns,
        max_budget_usd=settings.budget_usd_per_run,
        output_format={"type": "json_schema", "schema": SCHEMA},
        setting_sources=[],
    )
    data: dict = {"results": [], "needs_code": False}
    cost = 0.0
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            cost = message.total_cost_usd or 0.0
            payload = message.structured_output
            if payload is None and message.result:
                try:
                    payload = json.loads(message.result)
                except json.JSONDecodeError:
                    m = re.search(r"\{.*\}", message.result, re.S)
                    payload = json.loads(m.group(0)) if m else None
            if isinstance(payload, dict):
                data = payload
    store = get_store(project_name)
    result = AiSearchResult(
        question=question.strip(),
        stage=stage,  # type: ignore[arg-type]
        results=validate_hits(store, list(data.get("results") or [])),
        needs_code=bool(data.get("needs_code")),
        cost_usd=cost,
        model=settings.model,
        generated_at=now(),
        scan_key=scan_key(store),
    )
    save_result(project_name, result)
    return result


async def search_summaries(
    project_name: str, question: str, settings: Settings | None = None
) -> AiSearchResult:
    settings = settings or config.load_settings()
    cached = load_result(project_name, question, "summaries")
    if cached:
        return cached
    ok, why = check_available()
    if not ok:
        raise AiUnavailable(why)
    store = get_store(project_name)
    prompt = f"Question: {question.strip()}\n\nProject index:\n{build_index(store, question)}"
    return await _run(
        project_name, question, "summaries", prompt, settings, tools=[], max_turns=2, add_dirs=[]
    )


async def search_code(
    project_name: str, question: str, settings: Settings | None = None
) -> AiSearchResult:
    settings = settings or config.load_settings()
    cached = load_result(project_name, question, "code")
    if cached:
        return cached
    ok, why = check_available()
    if not ok:
        raise AiUnavailable(why)
    store = get_store(project_name)
    prior = load_result(project_name, question, "summaries")
    roots = []
    for repo in store.project.repos:
        p = repo.checkouts.get(repo.active_branch)
        if p:
            roots.append(f"{repo.name}: {p}")
    prompt = (
        f"Question: {question.strip()}\n\nRepository checkouts:\n"
        + "\n".join(roots)
        + "\n\nPreliminary results from names and summaries:\n"
        + json.dumps([h.model_dump() for h in prior.results] if prior else [], indent=1)
        + f"\n\nProject index:\n{build_index(store, question, max_chars=60_000)}"
    )
    return await _run(
        project_name,
        question,
        "code",
        prompt,
        settings,
        tools=["Read", "Grep", "Glob"],
        max_turns=30,
        add_dirs=[r.split(": ", 1)[1] for r in roots],
    )
