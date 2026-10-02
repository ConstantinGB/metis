"""Phase 13-16: docs, roles, framework edges, call precision, tests, git signals, metrics."""

from __future__ import annotations

from conftest import run_git

from metis.scan import gitsignals
from metis.scan.runner import scan_path


def edge(scan, source, target, kind):
    return next(
        (e for e in scan.edges if e.source == source and e.target == target and e.kind == kind),
        None,
    )


def test_docs_all_languages(fixture_repo):
    go = scan_path(fixture_repo("goproj"), "goproj", "main", go_module="example.com/gamma")
    store = go.file_by_path()["internal/store/store.go"]
    docs = {s.name: s.docstring for s in store.symbols}
    assert docs["New"] == "New creates an empty Store."
    assert docs["Store"] == "Store keeps key/value pairs in memory."
    js = scan_path(fixture_repo("jsproj"), "jsproj", "main", package_names=["beta"])
    util = js.file_by_path()["src/util.js"]
    assert {s.name: s.docstring for s in util.symbols}[
        "helper"
    ] == "Wraps the app with shared helpers."
    py = scan_path(fixture_repo("pyproj"), "pyproj", "main", package_names=["alpha"])
    files = py.file_by_path()
    assert files["alpha/core.py"].header_doc == "Core loading logic."
    assert {s.name: s.docstring for s in files["scripts/lib.sh"].symbols}[
        "log"
    ] == "Print a log line with the alpha prefix."
    web = scan_path(fixture_repo("webproj"), "webproj", "main", package_names=["webproj", "app"])
    wf = web.file_by_path()
    # the comment right above the first CREATE documents that table, not the file
    assert wf["schema.sql"].header_doc is None
    assert {s.name: s.docstring for s in wf["schema.sql"].symbols}[
        "customers"
    ] == "Customers who can log in."
    assert {s.name: s.docstring for s in wf["schema.sql"].symbols}[
        "audit_log"
    ] == "Every change made through the API."
    assert wf["server.js"].header_doc == "Tiny Express server that fronts the API."
    assert wf["app/main.py"].header_doc.startswith("HTTP API for customers.")


def test_roles_and_title_hints(fixture_repo):
    py = scan_path(fixture_repo("pyproj"), "pyproj", "main", package_names=["alpha"])
    f = py.file_by_path()
    assert (
        "entry" in f["alpha/cli.py"].roles
        and f["alpha/cli.py"].title_hint == "Entry point - Python"
    )
    assert (
        f[".github/workflows/ci.yml"].roles == ["ci", "config"]
        or "ci" in f[".github/workflows/ci.yml"].roles
    )
    assert f[".github/workflows/ci.yml"].title_hint == "CI workflow - GitHub Actions"
    assert "test" in f["tests/test_core.py"].roles
    assert "schema" in f["db/schema.sql"].roles
    assert "script" in f["scripts/run.sh"].roles
    assert f["alpha/__init__.py"].title_hint == "Package marker - Python"
    assert "build" in f["pyproject.toml"].roles
    web = scan_path(fixture_repo("webproj"), "webproj", "main", package_names=["webproj", "app"])
    w = web.file_by_path()
    assert "route" in w["app/main.py"].roles and "entry" in w["app/main.py"].roles
    assert w["app/main.py"].title_hint == "Entry point - Python"
    assert w["gen/api_pb2.py"].generated and "generated" in w["gen/api_pb2.py"].roles
    assert w["vendor/lib/helper.py"].vendored
    assert "route" in w["server.js"].roles and "entry" in w["server.js"].roles
    assert w[".env"].title_hint == "Environment file - dotenv"
    assert w["docker-compose.yml"].title_hint == "Service composition - Docker Compose"


def test_framework_edges(fixture_repo):
    web = scan_path(fixture_repo("webproj"), "webproj", "main", package_names=["webproj", "app"])
    w = web.file_by_path()
    routes = {s.name: s for s in w["app/main.py"].symbols if s.kind == "route"}
    assert set(routes) == {"GET /customers/{customer_id}", "POST /customers"}
    assert (
        edge(
            web,
            routes["GET /customers/{customer_id}"].id,
            "s:webproj:app/main.py#get_customer",
            "handles",
        ).confidence
        == "exact"
    )
    js_routes = {s.name for s in w["server.js"].symbols if s.kind == "route"}
    assert js_routes == {"GET /health", "POST /echo"}
    assert edge(web, "s:webproj:server.js#GET /health", "s:webproj:server.js#health", "handles")
    # env keys: readers and definers
    # module-level reads are attributed to the variable that stores them
    assert edge(web, "s:webproj:app/main.py#DATABASE_URL", "env:DATABASE_URL", "references")
    assert edge(web, "s:webproj:app/main.py#DEBUG", "env:DEBUG", "references")
    assert edge(web, "f:webproj:server.js", "env:PORT", "references")
    assert edge(web, "f:webproj:.env", "env:DATABASE_URL", "defines")
    assert edge(web, "f:webproj:.env", "env:PORT", "defines")
    assert edge(web, "f:webproj:docker-compose.yml", "env:DEBUG", "defines")
    assert web.envs == ["DATABASE_URL", "DEBUG", "PORT"]
    # SQL parsed from string literals: exact, and multi-line
    e = edge(web, "s:webproj:app/db.py#QUERY", "s:webproj:schema.sql#customers", "references")
    assert e and e.confidence == "exact"
    assert edge(web, "s:webproj:app/db.py#INSERT", "s:webproj:schema.sql#audit_log", "references")
    # CLI command
    cmd = next(s for s in w["app/cli.py"].symbols if s.kind == "command")
    assert cmd.name == "sync"
    assert edge(web, cmd.id, "s:webproj:app/cli.py#cmd_sync", "handles").confidence == "exact"


def test_call_precision(fixture_repo):
    web = scan_path(fixture_repo("webproj"), "webproj", "main", package_names=["webproj", "app"])
    # receiver tracking: svc = Service(); svc.run() -> Service.run, not Other.run
    assert (
        edge(
            web,
            "s:webproj:app/main.py#get_customer",
            "s:webproj:app/services.py#Service.run",
            "calls",
        ).confidence
        == "exact"
    )
    assert (
        edge(
            web,
            "s:webproj:app/main.py#get_customer",
            "s:webproj:app/services.py#Other.run",
            "calls",
        )
        is None
    )
    assert (
        edge(
            web,
            "s:webproj:app/main.py#create_customer",
            "s:webproj:app/services.py#Other.run",
            "calls",
        ).confidence
        == "exact"
    )
    # self.helper() inside the class
    assert (
        edge(
            web,
            "s:webproj:app/services.py#Service.run",
            "s:webproj:app/services.py#Service.helper",
            "calls",
        ).confidence
        == "exact"
    )
    # a parameter shadowing an imported function produces no edge
    assert (
        edge(web, "s:webproj:app/main.py#process", "s:webproj:app/db.py#fetch_customer", "calls")
        is None
    )
    assert (
        edge(
            web, "s:webproj:app/main.py#get_customer", "s:webproj:app/db.py#fetch_customer", "calls"
        ).confidence
        == "exact"
    )
    # deep re-export: tests import get_customer via app/__init__ -> app/main
    assert edge(
        web, "f:webproj:tests/test_main.py", "s:webproj:app/main.py#get_customer", "imports"
    )
    # module alias receiver in Go: store.New() -> exact
    go = scan_path(fixture_repo("goproj"), "goproj", "main", go_module="example.com/gamma")
    assert (
        edge(
            go, "s:goproj:main.go#main", "s:goproj:internal/store/store.go#New", "calls"
        ).confidence
        == "exact"
    )
    # tsconfig paths alias
    js = scan_path(fixture_repo("jsproj"), "jsproj", "main", package_names=["beta"])
    assert edge(js, "f:jsproj:src/app.ts", "f:jsproj:src/lib/other/index.js", "imports")


def test_tests_metrics_git(fixture_repo):
    root = fixture_repo("webproj")
    web = scan_path(root, "webproj", "main", package_names=["webproj", "app"])
    assert (
        edge(
            web,
            "s:webproj:tests/test_main.py#test_get_customer",
            "s:webproj:app/main.py#get_customer",
            "tests",
        ).confidence
        == "exact"
    )
    assert edge(
        web,
        "s:webproj:tests/test_main.py#TestService",
        "s:webproj:app/services.py#Service",
        "tests",
    )
    main = web.file_by_path()["app/main.py"]
    assert main.metrics["symbols"] >= 5 and main.metrics["fan_out"] >= 2
    get = next(s for s in main.symbols if s.name == "get_customer")
    assert get.metrics["lines"] == 5
    # git signals on a repo with two commits touching two files together
    (root / "app" / "db.py").write_text((root / "app" / "db.py").read_text() + "\n# touched\n")
    (root / "app" / "main.py").write_text((root / "app" / "main.py").read_text() + "\n# touched\n")
    run_git("commit", "-qam", "touch both", cwd=root)
    sig = gitsignals.collect(root, "webproj", "main", known_files={f.path for f in web.files})
    assert sig.commits_seen == 2
    assert sig.files["app/db.py"].commits_total == 2 and sig.files["app/db.py"].commits_90d == 2
    assert ("app/main.py", 2) in sig.files["app/db.py"].co_changes
    assert sig.files["README.md"].commits_total == 1
