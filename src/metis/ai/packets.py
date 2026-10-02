"""Briefing packets: everything the scan knows about one file, as one prompt for a no-tool call."""

from __future__ import annotations

import json
import posixpath
from collections import defaultdict
from typing import Any

from metis.model import AiFileSummary, FileEntry, LocalScan, parse_id
from metis.store import ProjectStore

PACKET_CHARS = 48_000  # ~12k tokens
BODY_LINES = 80
FILE_TEXT_LINES = 200

PACKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "keep": {"type": "boolean", "description": "true when the draft is already right"},
        "unclear": {
            "type": "boolean",
            "description": "true when the packet is not enough to explain the file",
        },
        "reason": {"type": "string"},
        "summary": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "purpose": {"type": "string"},
                "functions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol_id": {"type": "string"},
                            "explanation": {"type": "string"},
                            "inputs_from": {"type": "array", "items": {"type": "string"}},
                            "outputs_to": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["symbol_id", "explanation"],
                    },
                },
                "connections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string"},
                            "direction": {"type": "string", "enum": ["uses", "used_by"]},
                            "why": {"type": "string"},
                        },
                        "required": ["target", "direction", "why"],
                    },
                },
                "rejected_targets": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
        },
    },
    "required": ["keep", "unclear"],
}


def first_paragraph(text: str | None) -> str | None:
    if not text:
        return None
    return text.split("\n\n", 1)[0].strip() or None


def draft_summary(fe: FileEntry) -> dict[str, Any]:
    """What the scan already knows, in the shape of a summary. The model corrects it."""
    draft: dict[str, Any] = {
        "title": fe.title_hint or "",
        "purpose": first_paragraph(fe.header_doc) or "",
        "functions": [],
        "connections": [],
        "rejected_targets": [],
        "notes": None,
    }
    for s in fe.symbols:
        if s.kind in ("function", "method", "class", "table", "view", "procedure") and s.docstring:
            draft["functions"].append(
                {
                    "symbol_id": s.id,
                    "explanation": first_paragraph(s.docstring) or "",
                    "inputs_from": [],
                    "outputs_to": [],
                }
            )
    return draft


def waves(scan: LocalScan, paths: list[str]) -> list[list[str]]:
    """Group files so each wave only depends on earlier waves (leaves first); cycles end up last."""
    wanted = set(paths)
    deps: dict[str, set[str]] = defaultdict(set)
    for e in scan.edges:
        if e.kind != "imports":
            continue
        sp, tp = parse_id(e.source), parse_id(e.target)
        if sp.path in wanted and tp.path in wanted and sp.path != tp.path and tp.repo == scan.repo:
            deps[sp.path].add(tp.path)
    remaining = set(wanted)
    out: list[list[str]] = []
    while remaining:
        ready = sorted(p for p in remaining if not (deps.get(p, set()) & remaining))
        if not ready:
            ready = sorted(remaining)  # cycle: take them all
        out.append(ready)
        remaining -= set(ready)
    return out


def build_packet(store: ProjectStore, scan: LocalScan, fe: FileEntry, text: str) -> str:
    repo = scan.repo
    lines = text.splitlines()
    parts: list[str] = []
    parts.append(
        f"# File {fe.path}  ({fe.language or 'unknown'}, {fe.line_count} lines, id f:{repo}:{fe.path})"
    )
    if fe.roles:
        parts.append("Roles: " + ", ".join(fe.roles))
    if fe.header_doc:
        parts.append("Header documentation:\n" + fe.header_doc)
    if fe.metrics:
        parts.append(
            f"Used by {fe.metrics.get('fan_in', 0)} files; uses {fe.metrics.get('fan_out', 0)} files."
        )

    imps = []
    for i in fe.imports:
        if i.resolved_to and not i.is_stdlib:
            label = i.resolved_to.id
            if i.resolved_to.kind == "file":
                ref = parse_id(i.resolved_to.id)
                other = (
                    store.ai_summary(ref.repo or "", ref.path or "")
                    if ref.repo and ref.path
                    else None
                )
                if other:
                    label += f" — {other.title}: {other.purpose}"
            imps.append(f"- {i.raw}  -> {label}")
    if imps:
        parts.append(
            "Imports (with what METIS already knows about the target):\n" + "\n".join(imps[:60])
        )

    out_edges = [
        e
        for src in (fe.id, *(s.id for s in fe.symbols))
        for e in store.outgoing(src)
        if e.kind != "imports"
    ]
    inc_edges = [e for src in (fe.id, *(s.id for s in fe.symbols)) for e in store.incoming(src)]
    if out_edges:
        parts.append(
            "Outgoing connections:\n"
            + "\n".join(
                f"- {e.source} {e.kind} {e.target}{' (guess)' if e.confidence == 'heuristic' else ''}"
                for e in out_edges[:60]
            )
        )
    if inc_edges:
        parts.append(
            "Incoming connections:\n"
            + "\n".join(
                f"- {e.source} {e.kind} {e.target}{' (guess)' if e.confidence == 'heuristic' else ''}"
                for e in inc_edges[:60]
            )
        )

    sym_parts: list[str] = []
    for s in fe.symbols:
        if s.kind in ("key",):
            continue
        entry = f"- {s.id} [{s.kind}] lines {s.start_line}-{s.end_line}: {s.signature}"
        if s.docstring:
            entry += "\n  doc: " + s.docstring.replace("\n", "\n       ")
        needs_body = s.kind in ("function", "method") and (
            not s.docstring or "entry" in fe.roles or "route" in fe.roles
        )
        if (
            needs_body
            and s.parent is None
            or (needs_body and s.kind == "method" and not s.docstring)
        ):
            body = lines[s.start_line - 1 : s.end_line][:BODY_LINES]
            entry += "\n  body:\n" + "\n".join("    " + ln for ln in body)
            if s.end_line - s.start_line + 1 > BODY_LINES:
                entry += "\n    ... (truncated)"
        sym_parts.append(entry)
    if sym_parts:
        parts.append("Symbols:\n" + "\n".join(sym_parts))
    elif lines:
        parts.append(
            "Content:\n"
            + "\n".join(lines[:FILE_TEXT_LINES])
            + ("\n... (truncated)" if len(lines) > FILE_TEXT_LINES else "")
        )

    parts.append(
        "Draft summary from the scan (correct or confirm it):\n"
        + json.dumps(draft_summary(fe), ensure_ascii=False)
    )
    packet = "\n\n".join(parts)
    if len(packet) > PACKET_CHARS:
        packet = packet[:PACKET_CHARS] + "\n... (packet truncated; set unclear if this matters)"
    return packet


def merge(draft: dict[str, Any], partial: dict[str, Any] | None, keep: bool) -> dict[str, Any]:
    """Apply the model's answer to the draft. `keep` protects the draft title and purpose; additive
    fields (functions, connections, rejected targets, notes) are merged in either way."""
    merged = dict(draft)
    merged["functions"] = list(draft.get("functions", []))
    for k, v in (partial or {}).items():
        if v in (None, "", [], {}):
            continue
        if keep and k in ("title", "purpose"):
            continue
        if k == "functions" and isinstance(v, list):
            known = {f.get("symbol_id"): i for i, f in enumerate(merged["functions"])}
            for fn in v:
                sid = fn.get("symbol_id") if isinstance(fn, dict) else None
                if sid in known:
                    merged["functions"][known[sid]] = {**merged["functions"][known[sid]], **fn}
                else:
                    merged["functions"].append(fn)
        else:
            merged[k] = v
    if not merged.get("title"):
        merged["title"] = "File"
    if not merged.get("purpose"):
        merged["purpose"] = "No description available yet."
    return merged


def summary_from(fe: FileEntry, merged: dict[str, Any], model: str, source: str) -> AiFileSummary:
    known = {s.id for s in fe.symbols}
    functions = [f for f in merged.get("functions", []) if f.get("symbol_id") in known]
    return AiFileSummary(
        path=fe.path,
        content_hash=fe.content_hash,
        title=str(merged["title"])[:120],
        purpose=str(merged["purpose"])[:1200],
        functions=functions,
        connections=merged.get("connections", []),
        rejected_targets=merged.get("rejected_targets", []),
        notes=merged.get("notes") or None,
        model=model,
        source=source,  # type: ignore[arg-type]
    )


def basename(path: str) -> str:
    return posixpath.basename(path)
