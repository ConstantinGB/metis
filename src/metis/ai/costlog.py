"""Append-only log of every model call, so cost per file class can be measured."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from metis import store as st
from metis.model import now


def log_path(project: str, repo: str, branch: str) -> Path:
    return st.ai_dir(project, repo, branch) / "_log.jsonl"


def log_call(
    project: str,
    repo: str,
    branch: str,
    stage: str,
    model: str,
    path: str | None,
    cost_usd: float,
    usage: dict[str, Any] | None,
    note: str | None = None,
) -> None:
    usage = usage or {}
    rec = {
        "at": now().isoformat(),
        "stage": stage,
        "model": model,
        "path": path,
        "cost_usd": round(cost_usd, 6),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read": usage.get("cache_read_input_tokens"),
        "cache_write": usage.get("cache_creation_input_tokens"),
        "note": note,
    }
    with log_path(project, repo, branch).open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


def summarise(project: str, repo: str, branch: str) -> dict[str, dict[str, float]]:
    """Per stage: calls, cost, tokens."""
    p = log_path(project, repo, branch)
    out: dict[str, dict[str, float]] = {}
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        s = out.setdefault(
            rec.get("stage", "?"),
            {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0},
        )
        s["calls"] += 1
        s["cost_usd"] += rec.get("cost_usd") or 0.0
        s["input_tokens"] += rec.get("input_tokens") or 0
        s["output_tokens"] += rec.get("output_tokens") or 0
    return out
