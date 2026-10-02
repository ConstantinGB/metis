from __future__ import annotations

from metis.scan.imports import RepoInfo
from metis.scan.runner import scan_path


def edges(scan, kind=None):
    return {
        (e.source, e.target, e.kind, e.confidence)
        for e in scan.edges
        if kind is None or e.kind == kind
    }


def has_edge(scan, source, target, kind, confidence=None):
    return any(
        e.source == source
        and e.target == target
        and e.kind == kind
        and (confidence is None or e.confidence == confidence)
        for e in scan.edges
    )


def test_python_symbols_and_edges(fixture_repo):
    root = fixture_repo("pyproj")
    scan = scan_path(root, "pyproj", "main", package_names=["alpha"])
    files = scan.file_by_path()
    core = files["alpha/core.py"]
    names = {s.qualified_name: s for s in core.symbols}
    assert names["Loader"].kind == "class"
    assert names["Loader.load"].kind == "method"
    assert names["Loader.load"].docstring == "Read the JSON file."
    assert names["load"].kind == "function"
    assert names["DEFAULT_PATH"].kind == "constant"
    assert names["Loader.load"].parent == names["Loader"].id

    cli = files["alpha/cli.py"]
    mods = {i.module: i for i in cli.imports}
    assert mods["sys"].is_stdlib
    assert mods[".core"].resolved_to and mods[".core"].resolved_to.id == "f:pyproj:alpha/core.py"
    assert mods["alpha.db"].resolved_to.id == "f:pyproj:alpha/db.py"

    assert has_edge(scan, "f:pyproj:alpha/cli.py", "f:pyproj:alpha/core.py", "imports", "exact")
    assert has_edge(
        scan, "f:pyproj:alpha/cli.py", "s:pyproj:alpha/core.py#load", "imports", "exact"
    )
    assert has_edge(
        scan, "s:pyproj:alpha/cli.py#main", "s:pyproj:alpha/core.py#load", "calls", "exact"
    )
    assert has_edge(
        scan, "s:pyproj:alpha/cli.py#main", "s:pyproj:alpha/db.py#fetch_customers", "calls"
    )
    assert has_edge(
        scan, "s:pyproj:alpha/core.py#Loader", "s:pyproj:alpha/core.py#Base", "inherits", "exact"
    )
    assert has_edge(
        scan, "s:pyproj:alpha/core.py#load", "s:pyproj:alpha/core.py#Loader", "calls", "exact"
    )
    # stdlib produces no edge but is listed as an import
    assert not any(e.target == "x:sys" for e in scan.edges)
    assert "x:sys" not in scan.externals


def test_ops_languages(fixture_repo):
    root = fixture_repo("pyproj")
    scan = scan_path(root, "pyproj", "main", package_names=["alpha"])
    # yaml -> runs
    assert has_edge(
        scan, "f:pyproj:.github/workflows/ci.yml", "f:pyproj:scripts/run.sh", "runs", "exact"
    )
    ci = scan.file_by_path()[".github/workflows/ci.yml"]
    assert {s.name for s in ci.symbols} == {"name", "on", "jobs"}
    # bash -> includes / runs
    assert has_edge(scan, "f:pyproj:scripts/run.sh", "f:pyproj:scripts/lib.sh", "includes", "exact")
    assert has_edge(scan, "f:pyproj:scripts/run.sh", "f:pyproj:alpha/cli.py", "runs", "exact")
    lib = scan.file_by_path()["scripts/lib.sh"]
    assert [s.name for s in lib.symbols] == ["log"]
    # sql -> tables, references
    schema = scan.file_by_path()["db/schema.sql"]
    kinds = {s.name: s.kind for s in schema.symbols}
    assert kinds == {"customers": "table", "orders": "table", "active_customers": "view"}
    assert has_edge(
        scan, "f:pyproj:db/schema.sql", "s:pyproj:db/schema.sql#customers", "references", "exact"
    )
    # the string literal lives inside the module-level QUERY variable, so that symbol is the source
    assert has_edge(
        scan,
        "s:pyproj:alpha/db.py#QUERY",
        "s:pyproj:db/schema.sql#customers",
        "references",
        "exact",
    )


def test_javascript(fixture_repo):
    root = fixture_repo("jsproj")
    scan = scan_path(root, "jsproj", "main", package_names=["beta"])
    assert has_edge(scan, "f:jsproj:src/index.js", "f:jsproj:src/util.js", "imports", "exact")
    assert has_edge(
        scan, "f:jsproj:src/index.js", "s:jsproj:src/util.js#helper", "imports", "exact"
    )
    assert has_edge(
        scan, "f:jsproj:src/index.js", "f:jsproj:src/lib/other/index.js", "imports", "exact"
    )
    assert has_edge(scan, "f:jsproj:src/index.js", "x:express", "imports", "exact")
    assert has_edge(scan, "f:jsproj:src/app.ts", "f:jsproj:src/util.js", "imports", "exact")
    assert has_edge(scan, "f:jsproj:src/app.ts", "f:jsproj:src/types.ts", "imports", "exact")
    assert has_edge(
        scan, "s:jsproj:src/index.js#start", "s:jsproj:src/util.js#helper", "calls", "exact"
    )
    assert "express" in scan.externals
    app = scan.file_by_path()["src/app.ts"]
    assert {s.qualified_name for s in app.symbols} >= {"App", "App.boot"}


def test_go(fixture_repo):
    root = fixture_repo("goproj")
    scan = scan_path(root, "goproj", "main", go_module="example.com/gamma")
    main = scan.file_by_path()["main.go"]
    mods = {i.module: i for i in main.imports}
    assert mods["fmt"].is_stdlib
    assert mods["example.com/gamma/internal/store"].resolved_to.id == "d:goproj:internal/store"
    assert has_edge(scan, "f:goproj:main.go", "d:goproj:internal/store", "imports", "exact")
    assert has_edge(scan, "s:goproj:main.go#main", "s:goproj:internal/store/store.go#New", "calls")
    store = scan.file_by_path()["internal/store/store.go"]
    assert {s.name for s in store.symbols} >= {"Store", "New", "Get"}


def test_cross_repo(fixture_repo):
    alpha_root = fixture_repo("pyproj")
    delta_root = fixture_repo("delta")
    other = RepoInfo(name="pyproj", package_names=["alpha"], root=alpha_root)
    scan = scan_path(delta_root, "delta", "main", package_names=["delta"], other_repos=[other])
    use = scan.file_by_path()["delta/use_alpha.py"]
    mods = {i.module: i for i in use.imports}
    assert mods["alpha.core"].resolved_to.id == "f:pyproj:alpha/core.py"
    assert mods["requests"].resolved_to.id == "x:requests"
    assert has_edge(
        scan, "f:delta:delta/use_alpha.py", "f:pyproj:alpha/core.py", "imports", "exact"
    )
    assert scan.externals == ["requests"]


def test_deterministic(fixture_repo):
    root = fixture_repo("pyproj")
    exclude = {"scanned_at": True, "stats": {"duration_s"}}
    a = scan_path(root, "pyproj", "main").model_dump(exclude=exclude)
    b = scan_path(root, "pyproj", "main").model_dump(exclude=exclude)
    assert a == b


def test_non_git_folder(fixture_repo):
    root = fixture_repo("pyproj", init_git=False)
    scan = scan_path(root, "pyproj", "local")
    assert "alpha/core.py" in scan.file_by_path()
    assert ".github/workflows/ci.yml" in scan.file_by_path()
