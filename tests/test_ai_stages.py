"""Phase 17-19 offline tests: templates, packets, stale marking, cost log, LSP mapping."""

from __future__ import annotations

import pytest

from metis import store as st
from metis.ai import costlog, packets
from metis.ai.templates import template_summary
from metis.ai.tools import AiRunContext, record_summary
from metis.model import AiFileSummary, AiIndex
from metis.projects import registry
from metis.scan import lsp
from metis.scan.runner import scan_repo
from metis.store import get_store


@pytest.fixture
def web(metis_home, fixture_repo):
    root = fixture_repo("webproj")
    project = registry.create_project("demo")
    repo = registry.add_repo(project, str(root))
    scan = scan_repo(project, repo)
    return root, scan, get_store("demo")


def test_templates(web):
    root, scan, store = web
    f = scan.file_by_path()
    t = template_summary(
        f["app/__init__.py"], scan, store.read_source("webproj", "app/__init__.py")
    )
    assert t and t.source == "template" and t.title == "Package re-exports - Python"
    assert any(c.target == "f:webproj:app/main.py" for c in t.connections)
    g = template_summary(f["gen/api_pb2.py"], scan, store.read_source("webproj", "gen/api_pb2.py"))
    assert g and "Generated" in g.title and "protocol buffer" in g.purpose
    v = template_summary(f["vendor/lib/helper.py"], scan, "")
    assert v and v.title.startswith("Vendored")
    e = template_summary(f[".env"], scan, store.read_source("webproj", ".env"))
    assert (
        e
        and "2 environment variables" in e.purpose
        and any(c.target == "env:PORT" for c in e.connections)
    )
    assert (
        template_summary(f["app/main.py"], scan, store.read_source("webproj", "app/main.py"))
        is None
    )


def test_packets_and_waves(web):
    root, scan, store = web
    f = scan.file_by_path()
    order = packets.waves(
        scan,
        ["app/main.py", "app/db.py", "app/services.py", "tests/test_main.py", "app/__init__.py"],
    )
    flat = [p for wave in order for p in wave]
    assert (
        flat.index("app/db.py")
        < flat.index("app/main.py")
        < flat.index("app/__init__.py")
        < flat.index("tests/test_main.py")
    )
    draft = packets.draft_summary(f["app/main.py"])
    assert draft["title"] == "Entry point - Python" and draft["purpose"].startswith(
        "HTTP API for customers."
    )
    assert any(
        fn["symbol_id"].endswith("#get_customer")
        and fn["explanation"] == "Return one customer by id."
        for fn in draft["functions"]
    )
    st.save_ai_summary(
        "demo",
        "webproj",
        "main",
        AiFileSummary(path="app/db.py", title="DB access - Python", purpose="Runs queries."),
    )
    packet = packets.build_packet(
        store, scan, f["app/main.py"], store.read_source("webproj", "app/main.py")
    )
    assert "Roles: entry, route" in packet
    assert "DB access - Python: Runs queries." in packet  # neighbour summary inlined
    assert (
        "s:webproj:app/main.py#create_customer" in packet and "body:" in packet
    )  # undocumented -> body included
    assert "Draft summary from the scan" in packet
    merged = packets.merge(draft, {"purpose": "Serves customers.", "notes": ""}, keep=False)
    assert merged["purpose"] == "Serves customers." and merged["title"] == "Entry point - Python"
    kept = packets.merge(
        draft,
        {
            "purpose": "ignored",
            "functions": [
                {"symbol_id": "s:webproj:app/main.py#create_customer", "explanation": "Adds one."},
                {
                    "symbol_id": "s:webproj:app/main.py#get_customer",
                    "explanation": "Reworded.",
                    "inputs_from": ["db"],
                },
            ],
        },
        keep=True,
    )
    assert kept["purpose"] == draft["purpose"]  # keep protects title and purpose
    by_id = {f["symbol_id"]: f for f in kept["functions"]}
    assert (
        by_id["s:webproj:app/main.py#create_customer"]["explanation"] == "Adds one."
    )  # additions merge in
    assert by_id["s:webproj:app/main.py#get_customer"]["inputs_from"] == ["db"]
    summ = packets.summary_from(f["app/main.py"], merged, "claude-haiku-4-5", "packet")
    assert summ.source == "packet" and summ.model == "claude-haiku-4-5"
    # unknown symbol ids in functions are dropped rather than rejected
    bad = packets.summary_from(
        f["app/main.py"],
        {**merged, "functions": [{"symbol_id": "s:nope", "explanation": "x"}]},
        "m",
        "packet",
    )
    assert bad.functions == []


def test_record_summary_and_costlog(web):
    root, scan, store = web
    f = scan.file_by_path()
    ctx = AiRunContext(
        project_name="demo",
        repo_name="webproj",
        branch="main",
        root=root,
        scan=scan,
        store=store,
        index=AiIndex(),
        pending={"app/db.py": f["app/db.py"]},
        model="m",
        total=1,
        budget=1.0,
    )
    s = AiFileSummary(
        path="app/db.py",
        content_hash=f["app/db.py"].content_hash,
        title="t",
        purpose="p",
        source="packet",
    )
    record_summary(ctx, s)
    assert ctx.pending == {} and ctx.submitted == 1 and ctx.index.counts == {"packet": 1}
    ctx.add_cost(0.05, "packet")
    assert ctx.remaining() == pytest.approx(0.95) and ctx.index.cost_by_source[
        "packet"
    ] == pytest.approx(0.05)
    costlog.log_call(
        "demo",
        "webproj",
        "main",
        "packet",
        "m",
        "app/db.py",
        0.05,
        {"input_tokens": 100, "output_tokens": 20},
    )
    costlog.log_call("demo", "webproj", "main", "agent", "m", None, 0.5, None)
    summary = costlog.summarise("demo", "webproj", "main")
    assert summary["packet"]["calls"] == 1 and summary["packet"]["input_tokens"] == 100
    assert summary["agent"]["cost_usd"] == pytest.approx(0.5)


def test_stale_marking(web):
    root, scan, store = web
    f = scan.file_by_path()
    for path in ("app/services.py", "app/main.py"):
        st.save_ai_summary(
            "demo",
            "webproj",
            "main",
            AiFileSummary(path=path, content_hash=f[path].content_hash, title="t", purpose="p"),
        )
    # change services.py's interface: add a method
    p = root / "app" / "services.py"
    p.write_text(p.read_text() + "\n    def extra(self):\n        return 3\n")
    project = registry.load_project("demo")
    scan_repo(project, project.repo("webproj"))
    main = st.load_ai_summary("demo", "webproj", "main", "app/main.py")
    assert main is not None and main.stale  # importer of a changed interface
    services = st.load_ai_summary("demo", "webproj", "main", "app/services.py")
    assert services is not None and not services.stale  # it changed itself, so it is simply pending
    # a comment-only change is not an interface change
    p.write_text(p.read_text() + "\n# nothing\n")
    main.stale = False
    st.save_ai_summary("demo", "webproj", "main", main)
    scan_repo(project, project.repo("webproj"))
    assert not st.load_ai_summary("demo", "webproj", "main", "app/main.py").stale


def test_lsp_mapping_and_noop(web, tmp_path):
    root, scan, store = web
    loc = {
        "uri": (root / "app" / "services.py").as_uri(),
        "range": {"start": {"line": 6, "character": 8}, "end": {"line": 6, "character": 11}},
    }
    sym = lsp.location_to_symbol(root, scan, loc)
    assert sym is not None and sym.qualified_name == "Service.run"
    assert (
        lsp.location_to_symbol(root, scan, {"uri": "file:///elsewhere/x.py", "range": loc["range"]})
        is None
    )
    before = [e.model_dump() for e in scan.edges]
    assert lsp.refine(root, scan, tmp_path, servers={}) == {}
    assert [e.model_dump() for e in scan.edges] == before
    # a guessed call site is a candidate for lookup
    work = lsp._targets_needing_help(scan)
    assert isinstance(work, dict)
