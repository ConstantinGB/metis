from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from metis import store as st
from metis.model import AiFileSummary, AiFunctionSummary, AiIndex, AiRepoOverview
from metis.projects import registry
from metis.render.markdown import render_card
from metis.scan.runner import scan_repo
from metis.store import get_store
from metis.web.app import create_app


@pytest.fixture
def project(metis_home, fixture_repo):
    root = fixture_repo("pyproj")
    project = registry.create_project("demo")
    repo = registry.add_repo(project, str(root))
    scan_repo(project, repo)
    return registry.load_project("demo")


def test_cards(project):
    store = get_store("demo")
    repo_card = render_card(store, "r:pyproj")
    assert repo_card.title == "pyproj"
    assert "node:f%3Apyproj%3AREADME.md" in repo_card.markdown
    file_card = render_card(store, "f:pyproj:alpha/cli.py")
    assert file_card.title == "Entry point - Python"
    assert "node:s%3Apyproj%3Aalpha%2Fcli.py%23main" in file_card.markdown
    assert "standard library" in file_card.markdown
    assert (
        "`from alpha.db import fetch_customers`" in file_card.markdown
    )  # no escapes in code spans
    assert "called by [alpha/cli.py]" not in file_card.markdown  # own-file edges are hidden
    assert file_card.source_node == "f:pyproj:alpha/cli.py"
    sym = render_card(store, "s:pyproj:alpha/core.py#Loader.load")
    assert "Read the JSON file." in sym.markdown
    assert sym.source_open
    assert "Part of" in sym.markdown
    ext = render_card(store, "x:pytest")
    assert ext.subtitle == "external dependency"
    d = render_card(store, "d:pyproj:alpha")
    assert "cli.py" in d.markdown


def test_cards_with_ai(project):
    st.save_ai_summary(
        "demo",
        "pyproj",
        "main",
        AiFileSummary(
            path="alpha/cli.py",
            title="CLI entry point - Python",
            purpose="Runs the loader from the shell.",
            functions=[
                AiFunctionSummary(
                    symbol_id="s:pyproj:alpha/cli.py#main",
                    explanation="Parses args.",
                    inputs_from=["alpha/core.py", "Loader.load", "the shell"],
                )
            ],
            notes="Exit code matters.",
        ),
    )
    st.save_ai_overview(
        "demo",
        "pyproj",
        "main",
        AiRepoOverview(
            repo="pyproj",
            branch="main",
            title="Alpha - sample",
            purpose="A sample.",
            entry_points=["s:pyproj:alpha/cli.py#main"],
            architecture="One layer.",
        ),
    )
    store = get_store("demo")
    card = render_card(store, "f:pyproj:alpha/cli.py")
    assert card.ai and card.title == "CLI entry point - Python"
    assert "Parses args." in card.markdown and "Exit code matters." in card.markdown
    repo_card = render_card(store, "r:pyproj")
    assert "Alpha - sample" in repo_card.markdown and "Where execution starts" in repo_card.markdown
    sym = render_card(store, "s:pyproj:alpha/cli.py#main")
    assert sym.ai and "Parses args." in sym.markdown
    assert "node:f%3Apyproj%3Aalpha%2Fcore.py" in sym.markdown  # loose path resolved to a link
    assert "node:s%3Apyproj%3Aalpha%2Fcore.py%23Loader.load" in sym.markdown
    assert "the shell" in sym.markdown


def test_web_pages(project):
    client = TestClient(create_app())
    home = client.get("/").text
    assert "demo" in home and 'data-theme="dark"' in home
    page = client.get("/p/demo").text
    assert "pyproj" in page and "Local Scan" in page
    browse = client.get("/p/demo/browse").text
    assert 'href="node:r%3Apyproj"' in browse and "core.py" in browse
    card = client.get("/p/demo/card", params={"node": "f:pyproj:alpha/core.py", "col": 1}).text
    assert 'data-col="1"' in card and "Loader" in card
    path = client.get("/p/demo/browse", params={"path": "r:pyproj,f:pyproj:alpha/core.py"}).text
    assert path.count('<section class="card"') == 2
    assert client.get("/p/demo/card", params={"node": "bogus"}).status_code == 400
    search = client.get("/p/demo/search-card", params={"q": "load"}).text
    assert "Loader.load" in search
    assert client.get("/p/demo/diff").status_code == 200
    assert client.get("/p/demo/jobs").status_code == 200
    settings = client.get("/settings").text
    assert "claude-sonnet-5" in settings
    r = client.post(
        "/settings",
        data={
            "model": "claude-opus-5",
            "budget_usd_per_run": 3,
            "max_ai_turns": 100,
            "max_file_bytes": 500000,
            "port": 8765,
            "theme": "light",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    after = client.get("/settings").text
    assert "claude-opus-5" in after and 'data-theme="light"' in after
    assert client.get("/p/nope").status_code == 404


def test_local_scan_job(project):
    with TestClient(create_app()) as client:
        r = client.post("/p/demo/scan", data={"repo_name": "pyproj"}, follow_redirects=False)
        assert r.status_code == 303
        import time

        for _ in range(50):
            html = client.get("/p/demo/jobs").text
            if 'data-active="false"' in html:
                break
            time.sleep(0.1)
        assert "local scan" in html and "done" in html


def test_ai_index_helpers(project):
    idx = AiIndex(hashes={"a": "b"}, total_cost_usd=1.5)
    st.save_ai_index("demo", "pyproj", "main", idx)
    assert st.load_ai_index("demo", "pyproj", "main").hashes == {"a": "b"}
    assert st.load_ai_summary("demo", "pyproj", "main", "missing.py") is None
