from __future__ import annotations

import asyncio
import json

import pytest

from metis import store as st
from metis.ai import runner
from metis.ai.tools import AiRunContext, MetisTools
from metis.model import AiIndex
from metis.projects import registry
from metis.scan.runner import scan_repo
from metis.store import get_store


@pytest.fixture
def ctx(metis_home, fixture_repo):
    root = fixture_repo("pyproj")
    project = registry.create_project("demo")
    repo = registry.add_repo(project, str(root))
    scan = scan_repo(project, repo)
    pending = {f.path: f for f in scan.files if runner.wants_summary(f)}
    progress: list[tuple[str, float]] = []
    return AiRunContext(
        project_name="demo",
        repo_name="pyproj",
        branch="main",
        root=root,
        scan=scan,
        store=get_store("demo"),
        index=AiIndex(),
        pending=pending,
        model="test-model",
        on_progress=lambda m, f: progress.append((m, f)),
        total=len(pending),
    ), progress


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def text_of(result):
    return result["content"][0]["text"]


def test_list_and_facts(ctx):
    context, _ = ctx
    tools = MetisTools(context)
    listing = text_of(run(tools.list_files({})))
    assert "alpha/cli.py" in listing and "README.md" in listing
    assert listing.index("README.md") < listing.index("alpha/cli.py")  # docs first
    assert "package-lock" not in listing
    facts = json.loads(text_of(run(tools.file_facts({"path": "alpha/cli.py"}))))
    assert facts["id"] == "f:pyproj:alpha/cli.py"
    assert any(s["id"] == "s:pyproj:alpha/cli.py#main" for s in facts["symbols"])
    assert any(e["target"] == "f:pyproj:alpha/core.py" for e in facts["outgoing"])
    bad = run(tools.file_facts({"path": "nope.py"}))
    assert bad.get("is_error")
    src = text_of(run(tools.read_file({"path": "alpha/core.py", "start_line": 1, "end_line": 3})))
    assert src.splitlines()[0].strip().startswith("1")
    assert "Core loading logic" in src
    conns = json.loads(text_of(run(tools.connections({"node_id": "s:pyproj:alpha/core.py#load"}))))
    assert any(e["source"] == "s:pyproj:alpha/cli.py#main" for e in conns["incoming"])


def test_submit_validates_and_saves(ctx):
    context, progress = ctx
    tools = MetisTools(context)
    bad = run(tools.submit_summary({"path": "alpha/cli.py", "summary": {"title": "x"}}))
    assert bad.get("is_error") and "purpose" in text_of(bad)
    bad_id = run(
        tools.submit_summary(
            {
                "path": "alpha/cli.py",
                "summary": {
                    "title": "t",
                    "purpose": "p",
                    "functions": [{"symbol_id": "s:nope", "explanation": "e"}],
                },
            }
        )
    )
    assert bad_id.get("is_error") and "unknown symbol ids" in text_of(bad_id)
    good = run(
        tools.submit_summary(
            {
                "path": "alpha/cli.py",
                "summary": json.dumps(
                    {
                        "title": "CLI - Python",
                        "purpose": "Runs things.",
                        "functions": [
                            {
                                "symbol_id": "s:pyproj:alpha/cli.py#main",
                                "explanation": "Entry point.",
                            }
                        ],
                        "connections": [
                            {
                                "target": "f:pyproj:alpha/core.py",
                                "direction": "uses",
                                "why": "loads data",
                            }
                        ],
                    }
                ),
            }
        )
    )
    assert not good.get("is_error")
    saved = st.load_ai_summary("demo", "pyproj", "main", "alpha/cli.py")
    assert saved and saved.title == "CLI - Python" and saved.model == "test-model"
    assert saved.content_hash == context.scan.file_by_path()["alpha/cli.py"].content_hash
    assert "alpha/cli.py" not in context.pending
    assert st.load_ai_index("demo", "pyproj", "main").hashes["alpha/cli.py"] == saved.content_hash
    assert progress and "alpha/cli.py" in progress[-1][0]

    ov = run(
        tools.submit_overview(
            {
                "overview": {
                    "title": "Alpha",
                    "purpose": "Sample.",
                    "entry_points": ["s:pyproj:alpha/cli.py#main"],
                    "architecture": "flat",
                }
            }
        )
    )
    assert not ov.get("is_error")
    assert st.load_ai_overview("demo", "pyproj", "main").title == "Alpha"


def test_wants_summary(ctx):
    context, _ = ctx
    files = context.scan.file_by_path()
    assert runner.wants_summary(files["alpha/core.py"])
    assert runner.wants_summary(files["README.md"])
    assert runner.wants_summary(files[".github/workflows/ci.yml"])
    assert runner.wants_summary(files["db/schema.sql"])
    assert not runner.wants_summary(files["pyproject.toml"])


def test_build_server(ctx):
    context, _ = ctx
    from metis.ai.tools import allowed_tool_names, build_server

    server = build_server(MetisTools(context))
    assert server["type"] == "sdk" and server["name"] == "metis"
    assert "mcp__metis__metis_submit_summary" in allowed_tool_names()
