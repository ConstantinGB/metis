from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from metis import store as st
from metis.ai import search as ai_search
from metis.model import AiFileSummary, AiSearchResult, SearchHit
from metis.projects import registry
from metis.render.markdown import node_title, render_node, search_card
from metis.render.source import render_node_source, render_source
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


def test_render_source_basic():
    html = render_source("def f(x):\n    return x\n", "python", {(1, "f"): ("s:r:a.py#f", "def")})
    assert 'data-line="1"' in html and 'data-line="2"' in html and 'data-line="3"' not in html
    assert '<a class="src-link def" href="node:s%3Ar%3Aa.py%23f">f</a>' in html
    assert 'class="t-k"' in html  # keyword span
    assert render_source("plain words", None).count("<span") >= 1


def test_source_links_from_scan(project):
    store = get_store("demo")
    html, lines = render_node_source(store, "f:pyproj:alpha/cli.py")
    assert lines == 17
    # definition of main links to its own card
    assert 'class="src-link def" href="node:s%3Apyproj%3Aalpha%2Fcli.py%23main">main</a>' in html
    # call site of load on line 11 links to core.load
    assert 'href="node:s%3Apyproj%3Aalpha%2Fcore.py%23load">load</a>' in html
    # imported names are clickable too
    assert 'href="node:s%3Apyproj%3Aalpha%2Fdb.py%23fetch_customers">fetch_customers</a>' in html
    # symbol source is just its range, starting at its first line
    shtml, slines = render_node_source(store, "s:pyproj:alpha/core.py#Loader.load")
    assert slines == 4 and 'data-line="21"' in shtml and 'data-line="20"' not in shtml
    assert render_node_source(store, "f:pyproj:nope.py") is None


def test_cards_carry_source_and_line_links(project):
    store = get_store("demo")
    card = render_node(store, "f:pyproj:alpha/cli.py")
    assert card.source_node == "f:pyproj:alpha/cli.py" and card.source_lines == 17
    assert "```" not in card.markdown  # source no longer embedded in Markdown
    assert "[line 11](line:11)" in card.markdown
    sym = render_node(store, "s:pyproj:alpha/core.py#Loader")
    assert sym.source_open and sym.source_lines == 10


def test_search_card(project):
    store = get_store("demo")
    card = search_card(store, "load")
    assert (
        card.node_id == "q:load"
        and "Loader.load" in card.markdown
        and "### functions" in card.markdown
    )
    assert render_node(store, "q:load").title == "Search: load"
    assert "Nothing matches" in search_card(store, "zzz").markdown
    assert node_title(store, "q:load") == "load"
    assert node_title(store, "f:pyproj:alpha/cli.py") == "cli.py"


def test_web_browse_tabs_and_source(project):
    client = TestClient(create_app())
    r = client.get(
        "/p/demo/browse",
        params={"tabs": "r%3Apyproj,f%3Apyproj%3Aalpha%2Fcli.py|q%3Aload", "active": 1},
    )
    assert r.status_code == 200
    state = json.loads(
        r.text.split('<script id="metis-state" type="application/json">')[1].split("</script>")[0]
    )
    assert state["active"] == 1 and state["tabs"][0]["ids"] == ["r:pyproj", "f:pyproj:alpha/cli.py"]
    assert state["tabs"][0]["title"] == "cli.py" and state["tabs"][1]["title"] == "load"
    assert (
        'data-node="q:load"' in r.text and 'data-node="r:pyproj"' not in r.text
    )  # only active tab rendered
    legacy = client.get("/p/demo/browse", params={"path": "r:pyproj"}).text
    assert 'data-node="r:pyproj"' in legacy and 'id="tabs"' in legacy
    row = client.get("/p/demo/row", params={"path": "r%3Apyproj,f%3Apyproj%3Aalpha%2Fcli.py"}).text
    assert row.count('<section class="card"') == 2 and 'details class="source"' in row
    src = client.get("/p/demo/source", params={"node": "f:pyproj:alpha/cli.py"}).text
    assert 'pre class="src"' in src and 'data-line="17"' in src
    assert "not available" in client.get("/p/demo/source", params={"node": "x:sys"}).text
    sc = client.get("/p/demo/search-card", params={"q": "load"}).text
    assert 'data-node="q:load"' in sc and "Loader.load" in sc
    assert (
        client.get("/p/demo/ai-search/card", params={"q": "backups"}).text.count(
            'data-stage="pending"'
        )
        == 1
    )


def test_ai_search_index_and_validation(project):
    store = get_store("demo")
    st.save_ai_summary(
        "demo",
        "pyproj",
        "main",
        AiFileSummary(
            path="alpha/db.py", title="DB helpers - Python", purpose="Runs the customers query."
        ),
    )
    index = ai_search.build_index(store, "customers query")
    assert "f:pyproj:alpha/db.py | DB helpers - Python: Runs the customers query." in index
    assert "symbols: main" in index
    # relevant files come first
    assert index.index("alpha/db.py") < index.index("scripts/lib.sh")
    small = ai_search.build_index(store, "customers", max_chars=600)
    assert "omitted" in small
    hits = ai_search.validate_hits(
        store,
        [
            {"node_id": "s:pyproj:alpha/core.py#load", "why": "defines load", "confidence": "high"},
            {"node_id": "s:pyproj:nope.py#x", "why": "bogus"},
            {"node_id": "alpha/db.py", "why": "loose path"},
            {"node_id": "s:pyproj:alpha/core.py#load", "why": "dup"},
            {"node_id": "Loader.load", "why": "loose name", "confidence": "weird"},
        ],
    )
    assert [h.node_id for h in hits] == [
        "s:pyproj:alpha/core.py#load",
        "f:pyproj:alpha/db.py",
        "s:pyproj:alpha/core.py#Loader.load",
    ]
    assert hits[2].confidence == "medium"


def test_ai_search_cache_and_card(project):
    store = get_store("demo")
    result = AiSearchResult(
        question="where is load",
        stage="summaries",
        needs_code=True,
        cost_usd=0.02,
        results=[
            SearchHit(node_id="s:pyproj:alpha/core.py#load", why="defines it", confidence="high")
        ],
        scan_key=ai_search.scan_key(store),
    )
    ai_search.save_result("demo", result)
    assert (
        ai_search.load_result("demo", "Where is LOAD", "summaries") is not None
    )  # case-insensitive key
    assert ai_search.latest_result("demo", "where is load").stage == "summaries"
    assert ai_search.total_search_cost("demo") == pytest.approx(0.02)
    card = render_node(store, "ai:where is load")
    assert (
        card.stage == "summaries"
        and 'form class="deeper"' in card.markdown
        and "defines it" in card.markdown
    )
    # a new scan invalidates the cache
    scan_repo(registry.load_project("demo"), registry.load_project("demo").repo("pyproj"))
    assert ai_search.load_result("demo", "where is load", "summaries") is None
