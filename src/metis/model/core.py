"""Pydantic models. These are the JSON contract between every layer of METIS."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 2


def now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- settings


class Settings(BaseModel):
    schema_version: int = SCHEMA_VERSION
    model: str = "claude-sonnet-5"
    packet_model: str = "claude-haiku-4-5"
    packet_concurrency: int = 4
    use_language_servers: bool = True
    budget_usd_per_run: float = 5.0
    max_ai_turns: int = 400
    max_file_bytes: int = 1_000_000
    port: int = 8765
    theme: Literal["dark", "light"] = "dark"


# --------------------------------------------------------------------------- projects


class RepoSource(BaseModel):
    kind: Literal["github", "local"]
    url: str | None = None
    path: str | None = None

    def describe(self) -> str:
        return self.url or self.path or "?"


class Repo(BaseModel):
    name: str
    source: RepoSource
    is_git: bool = True
    default_branch: str = "main"
    branches: list[str] = Field(default_factory=list)
    active_branch: str = "main"
    checkouts: dict[str, str] = Field(default_factory=dict)  # branch -> absolute path
    has_token: bool = False
    package_names: list[str] = Field(default_factory=list)
    go_module: str | None = None


class Project(BaseModel):
    schema_version: int = SCHEMA_VERSION
    name: str
    repos: list[Repo] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    def repo(self, name: str) -> Repo:
        for r in self.repos:
            if r.name == name:
                return r
        raise KeyError(name)


# --------------------------------------------------------------------------- local scan

SymbolKind = Literal[
    "function",
    "method",
    "class",
    "module",
    "interface",
    "type",
    "constant",
    "variable",
    "key",
    "table",
    "view",
    "procedure",
    "trigger",
    "index",
    "route",
    "command",
    "other",
]

TargetKind = Literal["file", "dir", "symbol", "repo", "external"]


class ResolvedTarget(BaseModel):
    kind: TargetKind
    id: str


class Symbol(BaseModel):
    id: str
    kind: SymbolKind
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    signature: str = ""
    docstring: str | None = None
    parent: str | None = None  # id of the enclosing symbol
    metrics: dict[str, int] = Field(default_factory=dict)  # lines, branches


class Import(BaseModel):
    raw: str
    module: str
    names: list[str] = Field(default_factory=list)
    line: int
    resolved_to: ResolvedTarget | None = None
    edge_kind: Literal["imports", "runs", "references", "includes"] = "imports"
    is_stdlib: bool = False


ReferenceKind = Literal["call", "class", "other", "handler", "env", "env_define", "table"]


class Reference(BaseModel):
    name: str
    line: int
    kind: ReferenceKind = "call"
    col: int = 0
    receiver: str | None = None  # `obj` in `obj.name(...)`, "self"/"this" for own methods


class FileEntry(BaseModel):
    id: str
    path: str
    language: str | None = None
    size: int = 0
    line_count: int = 0
    content_hash: str = ""
    is_doc: bool = False
    skipped_reason: str | None = None
    header_doc: str | None = None
    roles: list[str] = Field(default_factory=list)
    title_hint: str | None = None
    generated: bool = False
    vendored: bool = False
    metrics: dict[str, int] = Field(default_factory=dict)  # symbols, fan_in, fan_out
    symbols: list[Symbol] = Field(default_factory=list)
    imports: list[Import] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)


EdgeKind = Literal[
    "imports", "calls", "references", "inherits", "runs", "includes", "handles", "defines", "tests"
]
Confidence = Literal["exact", "heuristic"]


class Edge(BaseModel):
    source: str
    target: str
    kind: EdgeKind
    confidence: Confidence = "exact"
    line: int | None = None
    note: str | None = None

    def key(self) -> tuple[str, str, str, int]:
        return (self.source, self.target, self.kind, self.line or 0)


class ScanStats(BaseModel):
    files: int = 0
    parsed_files: int = 0
    symbols: int = 0
    edges: int = 0
    languages: dict[str, int] = Field(default_factory=dict)
    duration_s: float = 0.0
    unsupported_languages: list[str] = Field(default_factory=list)
    lsp: dict[str, int] = Field(default_factory=dict)  # language -> edges confirmed/added


class LocalScan(BaseModel):
    schema_version: int = SCHEMA_VERSION
    repo: str
    branch: str
    commit: str | None = None
    root: str
    scanned_at: datetime = Field(default_factory=now)
    files: list[FileEntry] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    externals: list[str] = Field(default_factory=list)
    envs: list[str] = Field(default_factory=list)
    stats: ScanStats = Field(default_factory=ScanStats)

    # convenience indexes (not serialised)
    def file_by_path(self) -> dict[str, FileEntry]:
        return {f.path: f for f in self.files}

    def symbols_by_id(self) -> dict[str, Symbol]:
        return {s.id: s for f in self.files for s in f.symbols}


# --------------------------------------------------------------------------- git signals


class FileGit(BaseModel):
    last_changed: datetime | None = None
    commits_90d: int = 0
    commits_total: int = 0
    authors: int = 0
    co_changes: list[tuple[str, int]] = Field(default_factory=list)  # (path, shared commits)


class GitSignals(BaseModel):
    schema_version: int = SCHEMA_VERSION
    repo: str
    branch: str
    generated_at: datetime = Field(default_factory=now)
    commits_seen: int = 0
    files: dict[str, FileGit] = Field(default_factory=dict)


# --------------------------------------------------------------------------- ai scan


class AiConnection(BaseModel):
    target: str = Field(description="Node id (f:, s:, d:, r:) or external name")
    direction: Literal["uses", "used_by"]
    why: str = Field(description="One sentence: what flows across this connection")


class AiFunctionSummary(BaseModel):
    symbol_id: str = Field(description="The symbol id from the local scan (s:repo:path#name)")
    explanation: str = Field(description="Plain-language explanation for a novice, 1-3 sentences")
    inputs_from: list[str] = Field(
        default_factory=list, description="Node ids or names this reads from"
    )
    outputs_to: list[str] = Field(
        default_factory=list, description="Node ids or names this writes/sends to"
    )


class AiFileSummary(BaseModel):
    schema_version: int = SCHEMA_VERSION
    path: str
    content_hash: str = ""
    title: str = Field(description='Short label like "Migration script - Python"')
    purpose: str = Field(description="One or two sentences on what the file does and why it exists")
    functions: list[AiFunctionSummary] = Field(default_factory=list)
    connections: list[AiConnection] = Field(default_factory=list)
    rejected_targets: list[str] = Field(
        default_factory=list,
        description="Node ids the local scan linked heuristically that are actually wrong",
    )
    notes: str | None = Field(
        default=None, description="Anything a novice should know before editing"
    )
    generated_at: datetime = Field(default_factory=now)
    model: str | None = None
    source: Literal["template", "packet", "agent"] = "agent"
    stale: bool = False


class AiRepoOverview(BaseModel):
    schema_version: int = SCHEMA_VERSION
    repo: str
    branch: str
    title: str
    purpose: str
    entry_points: list[str] = Field(
        default_factory=list, description="Node ids where execution starts"
    )
    architecture: str = Field(
        default="", description="Markdown: layers, main flows, where to look first"
    )
    generated_at: datetime = Field(default_factory=now)
    model: str | None = None


class AiIndex(BaseModel):
    schema_version: int = SCHEMA_VERSION
    hashes: dict[str, str] = Field(default_factory=dict)  # path -> content_hash summarised
    last_run: datetime | None = None
    total_cost_usd: float = 0.0
    runs: int = 0
    model: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)  # per source: template/packet/agent
    cost_by_source: dict[str, float] = Field(default_factory=dict)


class DiffExplanation(BaseModel):
    schema_version: int = SCHEMA_VERSION
    repo: str
    base: str
    head: str
    explanation: str
    generated_at: datetime = Field(default_factory=now)
    cost_usd: float = 0.0
    model: str | None = None


# --------------------------------------------------------------------------- ai search


class SearchHit(BaseModel):
    node_id: str
    why: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"


class AiSearchResult(BaseModel):
    schema_version: int = SCHEMA_VERSION
    question: str
    stage: Literal["summaries", "code"] = "summaries"
    results: list[SearchHit] = Field(default_factory=list)
    needs_code: bool = False
    cost_usd: float = 0.0
    model: str | None = None
    generated_at: datetime = Field(default_factory=now)
    scan_key: str = ""
