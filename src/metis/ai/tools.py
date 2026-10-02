"""The MCP tools the AI Scan agent uses to read scan facts and submit results."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from metis import store as st
from metis.model import (
    AiConnection,
    AiFileSummary,
    AiFunctionSummary,
    AiIndex,
    AiRepoOverview,
    FileEntry,
    LocalScan,
    now,
)
from metis.store import ProjectStore

MAX_READ_LINES = 800
MAX_READ_CHARS = 60_000


class SummaryInput(BaseModel):
    title: str = Field(description='Short label, e.g. "Migration script - Python"')
    purpose: str = Field(description="One or two sentences: what the file does and why it exists")
    functions: list[AiFunctionSummary] = Field(default_factory=list)
    connections: list[AiConnection] = Field(default_factory=list)
    rejected_targets: list[str] = Field(
        default_factory=list, description="Heuristic targets that are wrong"
    )
    notes: str | None = Field(default=None, description="What a novice should know before editing")


class OverviewInput(BaseModel):
    title: str = Field(
        description='One-line name for the repo, e.g. "Billing API - FastAPI service"'
    )
    purpose: str = Field(description="Two or three sentences on what the repo is for")
    entry_points: list[str] = Field(
        default_factory=list, description="Node ids where execution starts"
    )
    architecture: str = Field(description="Markdown: layers, main flows, where to look first")


def _schema(
    model: type[BaseModel], wrap_as: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    s = model.model_json_schema()
    defs = s.pop("$defs", {})
    props: dict[str, Any] = dict(extra or {})
    props[wrap_as] = s
    out: dict[str, Any] = {"type": "object", "properties": props, "required": list(props)}
    if defs:
        out["$defs"] = defs
    return out


@dataclass
class AiRunContext:
    project_name: str
    repo_name: str
    branch: str
    root: Path
    scan: LocalScan
    store: ProjectStore
    index: AiIndex
    pending: dict[str, FileEntry]
    model: str
    on_progress: Callable[[str, float], None] | None = None
    submitted: int = 0
    total: int = 0
    log: list[str] = field(default_factory=list)
    budget: float = 5.0
    spent: float = 0.0
    unclear: dict[str, str] = field(default_factory=dict)

    def remaining(self) -> float:
        return max(self.budget - self.spent, 0.0)

    def add_cost(self, cost: float, source: str) -> None:
        self.spent += cost
        self.index.total_cost_usd += cost
        self.index.cost_by_source[source] = self.index.cost_by_source.get(source, 0.0) + cost

    def progress(self, message: str) -> None:
        self.log.append(message)
        if self.on_progress:
            frac = (self.submitted / self.total) if self.total else 0.0
            self.on_progress(message, min(frac, 0.99))


def record_summary(ctx: AiRunContext, summary: AiFileSummary) -> None:
    """The one write path for file summaries: save, update the index, advance progress."""
    st.save_ai_summary(ctx.project_name, ctx.repo_name, ctx.branch, summary)
    ctx.index.hashes[summary.path] = summary.content_hash
    ctx.index.counts[summary.source] = ctx.index.counts.get(summary.source, 0) + 1
    st.save_ai_index(ctx.project_name, ctx.repo_name, ctx.branch, ctx.index)
    if ctx.pending.pop(summary.path, None) is not None:
        ctx.submitted += 1
    ctx.unclear.pop(summary.path, None)
    ctx.progress(f"{ctx.submitted}/{ctx.total} {summary.path} ({summary.source})")


def _text(payload: Any, is_error: bool = False) -> dict[str, Any]:
    text = (
        payload if isinstance(payload, str) else json.dumps(payload, indent=1, ensure_ascii=False)
    )
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["is_error"] = True
    return out


def _order(ctx: AiRunContext) -> list[FileEntry]:
    """Docs first, then files with the fewest in-repo imports (leaves before callers)."""
    out_count: dict[str, int] = {}
    for e in ctx.scan.edges:
        if e.kind == "imports" and e.source.startswith("f:") and e.target.startswith(("f:", "d:")):
            out_count[e.source] = out_count.get(e.source, 0) + 1
    return sorted(
        ctx.pending.values(), key=lambda f: (not f.is_doc, out_count.get(f.id, 0), f.path)
    )


class MetisTools:
    """Plain async methods; `build_server` wraps them as MCP tools. Tests call them directly."""

    def __init__(self, ctx: AiRunContext):
        self.ctx = ctx

    async def list_files(self, args: dict[str, Any]) -> dict[str, Any]:
        files = _order(self.ctx)
        lines = [f"{len(files)} files pending, {self.ctx.submitted} already submitted this run."]
        for f in files[:250]:
            lines.append(
                f"- {f.path}  [{f.language or 'unknown'}, {f.line_count} lines, "
                f"{len(f.symbols)} symbols]{'  (doc)' if f.is_doc else ''}"
            )
        if len(files) > 250:
            lines.append(f"... and {len(files) - 250} more (call again after submitting some).")
        return _text("\n".join(lines))

    async def file_facts(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        f = self.ctx.scan.file_by_path().get(path)
        if f is None:
            return _text(
                f"unknown path {path!r}; use paths exactly as metis_list_files prints them", True
            )
        st_ = self.ctx.store
        facts: dict[str, Any] = {
            "id": f.id,
            "path": f.path,
            "language": f.language,
            "lines": f.line_count,
            "is_doc": f.is_doc,
            "symbols": [
                {
                    "id": s.id,
                    "kind": s.kind,
                    "signature": s.signature,
                    "lines": [s.start_line, s.end_line],
                    **({"docstring": s.docstring} if s.docstring else {}),
                }
                for s in f.symbols[:120]
            ],
            "imports": [
                {
                    "raw": i.raw,
                    "line": i.line,
                    "resolved_to": i.resolved_to.id if i.resolved_to else None,
                    **({"stdlib": True} if i.is_stdlib else {}),
                }
                for i in f.imports[:80]
            ],
            "outgoing": [
                {"kind": e.kind, "target": e.target, "confidence": e.confidence, "line": e.line}
                for e in (st_.outgoing(f.id) + [x for s in f.symbols for x in st_.outgoing(s.id)])[
                    :80
                ]
            ],
            "incoming": [
                {"kind": e.kind, "source": e.source, "confidence": e.confidence}
                for e in (st_.incoming(f.id) + [x for s in f.symbols for x in st_.incoming(s.id)])[
                    :80
                ]
            ],
        }
        return _text(facts)

    async def read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if path not in self.ctx.scan.file_by_path():
            return _text(f"unknown path {path!r}", True)
        text = self.ctx.store.read_source(self.ctx.repo_name, path)
        if text is None:
            return _text("could not read file", True)
        lines = text.splitlines()
        start = max(int(args.get("start_line") or 1), 1)
        end = min(int(args.get("end_line") or len(lines)), len(lines))
        chunk = lines[start - 1 : end][:MAX_READ_LINES]
        body = "\n".join(f"{start + i:5d}  {line}" for i, line in enumerate(chunk))
        if len(body) > MAX_READ_CHARS:
            body = body[:MAX_READ_CHARS] + "\n... (truncated; request a smaller line range)"
        elif start - 1 + len(chunk) < end:
            body += f"\n... (showing {len(chunk)} of {end - start + 1} lines; request a range)"
        return _text(body)

    async def connections(self, args: dict[str, Any]) -> dict[str, Any]:
        node = str(args.get("node_id", "")).strip()
        st_ = self.ctx.store
        return _text(
            {
                "outgoing": [
                    {"kind": e.kind, "target": e.target, "confidence": e.confidence}
                    for e in st_.outgoing(node)[:100]
                ],
                "incoming": [
                    {"kind": e.kind, "source": e.source, "confidence": e.confidence}
                    for e in st_.incoming(node)[:100]
                ],
            }
        )

    async def submit_summary(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        f = self.ctx.scan.file_by_path().get(path)
        if f is None:
            return _text(f"unknown path {path!r}", True)
        raw = args.get("summary")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as e:
                return _text(f"summary must be a JSON object: {e}", True)
        try:
            data = SummaryInput.model_validate(raw)
        except ValidationError as e:
            return _text("summary rejected:\n" + e.json(indent=1), True)
        known = {s.id for s in f.symbols}
        bad = [x.symbol_id for x in data.functions if x.symbol_id not in known]
        if bad:
            return _text(
                f"unknown symbol ids for {path}: {bad}. Use ids from metis_file_facts.", True
            )
        summary = AiFileSummary(
            path=path,
            content_hash=f.content_hash,
            title=data.title,
            purpose=data.purpose,
            functions=data.functions,
            connections=data.connections,
            rejected_targets=data.rejected_targets,
            notes=data.notes,
            model=self.ctx.model,
            source="agent",
        )
        record_summary(self.ctx, summary)
        return _text(f"saved summary for {path}; {len(self.ctx.pending)} files still pending")

    async def known_summaries(self, args: dict[str, Any]) -> dict[str, Any]:
        lines = []
        for path in sorted(self.ctx.index.hashes):
            summ = st.load_ai_summary(
                self.ctx.project_name, self.ctx.repo_name, self.ctx.branch, path
            )
            if summ:
                lines.append(f"- {path}: {summ.title} — {summ.purpose[:140]}")
            if len(lines) >= 400:
                lines.append("... (more omitted)")
                break
        return _text("\n".join(lines) or "no summaries yet")

    async def submit_overview(self, args: dict[str, Any]) -> dict[str, Any]:
        raw = args.get("overview")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as e:
                return _text(f"overview must be a JSON object: {e}", True)
        try:
            data = OverviewInput.model_validate(raw)
        except ValidationError as e:
            return _text("overview rejected:\n" + e.json(indent=1), True)
        ov = AiRepoOverview(
            repo=self.ctx.repo_name,
            branch=self.ctx.branch,
            title=data.title,
            purpose=data.purpose,
            entry_points=data.entry_points,
            architecture=data.architecture,
            model=self.ctx.model,
            generated_at=now(),
        )
        st.save_ai_overview(self.ctx.project_name, self.ctx.repo_name, self.ctx.branch, ov)
        self.ctx.progress("repo overview saved")
        return _text(
            "overview saved"
            + (f"; {len(self.ctx.pending)} files still pending" if self.ctx.pending else "")
        )


SERVER_NAME = "metis"
TOOL_NAMES = [
    "metis_known_summaries",
    "metis_list_files",
    "metis_file_facts",
    "metis_read_file",
    "metis_connections",
    "metis_submit_summary",
    "metis_submit_repo_overview",
]


def allowed_tool_names() -> list[str]:
    return [f"mcp__{SERVER_NAME}__{n}" for n in TOOL_NAMES]


def build_server(tools: MetisTools):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    list_files = tool(
        "metis_list_files",
        "Files still waiting for a summary, dependencies first.",
        {"type": "object", "properties": {}},
    )(tools.list_files)
    file_facts = tool(
        "metis_file_facts",
        "Local Scan facts for one file: symbols with ids, imports, edges.",
        {"path": str},
    )(tools.file_facts)
    read_file = tool(
        "metis_read_file",
        "Read a repo file (repo-relative path), optionally a line range.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            "required": ["path"],
        },
    )(tools.read_file)
    connections = tool(
        "metis_connections", "Incoming and outgoing edges for a node id.", {"node_id": str}
    )(tools.connections)
    submit = tool(
        "metis_submit_summary",
        "Save the summary for one file. Validates and reports errors.",
        _schema(SummaryInput, "summary", {"path": {"type": "string"}}),
    )(tools.submit_summary)
    known = tool(
        "metis_known_summaries",
        "Titles and purposes of every file already summarised (from packets or earlier runs).",
        {"type": "object", "properties": {}},
    )(tools.known_summaries)
    overview = tool(
        "metis_submit_repo_overview",
        "Save the repository overview (call once, at the end).",
        _schema(OverviewInput, "overview"),
    )(tools.submit_overview)
    return create_sdk_mcp_server(
        name=SERVER_NAME,
        version="1.0.0",
        tools=[known, list_files, file_facts, read_file, connections, submit, overview],
    )
