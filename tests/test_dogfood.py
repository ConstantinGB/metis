"""Scan METIS's own source tree: a real, larger repo than the fixtures (regression for a
tree-sitter 0.26 crash that only showed up on files with many query matches)."""

from __future__ import annotations

from pathlib import Path

from metis.scan.runner import scan_path

ROOT = Path(__file__).resolve().parents[1]


def test_scan_metis_itself():
    scan = scan_path(ROOT, "metis", "main", package_names=["metis"])
    files = scan.file_by_path()
    assert "src/metis/scan/runner.py" in files
    names = {s.qualified_name for s in files["src/metis/ai/tools.py"].symbols}
    assert {"MetisTools", "MetisTools.submit_summary", "build_server"} <= names
    runner = files["src/metis/scan/runner.py"]
    assert any(
        i.resolved_to and i.resolved_to.id == "f:metis:src/metis/scan/linker.py"
        for i in runner.imports
    )
    assert any(
        e.source == "s:metis:src/metis/scan/runner.py#scan_path"
        and e.target == "s:metis:src/metis/scan/linker.py#link"
        for e in scan.edges
    )
    assert not scan.stats.unsupported_languages

    # `from metis.model import FileEntry` goes through model/__init__.py; follow the re-export
    def edge(source, target, kind):
        return next(
            (e for e in scan.edges if e.source == source and e.target == target and e.kind == kind),
            None,
        )

    assert edge(
        "f:metis:src/metis/scan/runner.py", "s:metis:src/metis/model/core.py#FileEntry", "imports"
    )
    call = edge(
        "s:metis:src/metis/scan/runner.py#scan_path",
        "s:metis:src/metis/model/core.py#FileEntry",
        "calls",
    )
    assert call is not None and call.confidence == "exact"
