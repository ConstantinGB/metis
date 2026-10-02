"""Turn imports and references into edges: imports, calls (receiver- and scope-aware), inherits,
handles (routes/commands), env keys, SQL tables, and tests-to-code."""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict

from metis.model import Edge, FileEntry, LocalScan, Symbol, env_id, parse_id
from metis.scan.imports.base import ScanContext
from metis.scan.symbols import enclosing_symbol

MAX_EDGES_PER_FILE = 400
MAX_AMBIGUOUS = 3
SELF_NAMES = {"self", "this", "cls"}
ASSIGN_RE = re.compile(
    r"^\s*(?:(?:const|let|var)\s+)?(?:self\.|this\.)?(\w+)\s*(?::\s*([\w.]+))?\s*:?=\s*(?:new\s+|await\s+)?([\w.]+)\s*\("
)
LOCAL_ASSIGN_RE = re.compile(r"^\s*(?:(?:const|let|var)\s+)?(\w+)\s*(?::\s*[^=]+?)?\s*:?=(?!=)")
PARAM_ANN_RE = re.compile(r"(\w+)\s*:\s*([A-Z][\w.]*)")
TEST_NAME_RE = re.compile(r"^(?:test_?|Test)(.+?)(?:_?test)?$|^(.+?)_test$", re.I)


class _Index:
    def __init__(self, ctx: ScanContext, scan: LocalScan):
        self.ctx = ctx
        self.by_path: dict[str, FileEntry] = {f.path: f for f in scan.files}
        self.file_index: dict[str, dict[str, list[Symbol]]] = {}
        self.global_index: dict[str, list[Symbol]] = defaultdict(list)
        self.dir_files: dict[str, list[str]] = defaultdict(list)
        self.by_qname: dict[str, dict[str, Symbol]] = {}
        for f in scan.files:
            idx: dict[str, list[Symbol]] = defaultdict(list)
            for s in f.symbols:
                idx[s.name].append(s)
                self.global_index[s.name].append(s)
            self.file_index[f.path] = idx
            self.by_qname[f.path] = {s.qualified_name: s for s in f.symbols}
            self.dir_files[posixpath.dirname(f.path)].append(f.path)
        self._lines: dict[str, list[str]] = {}

    def lines(self, path: str) -> list[str]:
        if path not in self._lines:
            text = self.ctx.read_text(path) or ""
            self._lines[path] = text.splitlines()
        return self._lines[path]

    def exported(
        self, path: str, name: str, imported: set[str], seen: set[str] | None = None
    ) -> list[Symbol]:
        """Top-level symbols called `name` in `path`, following re-exports to any depth."""
        seen = seen or set()
        if path in seen or path not in self.by_path:
            return []
        seen.add(path)
        direct = [s for s in self.file_index.get(path, {}).get(name, []) if s.parent is None]
        if direct:
            return direct
        for imp in self.by_path[path].imports:
            if imp.resolved_to is None or imp.resolved_to.kind != "file":
                continue
            if name not in imp.names and "*" not in imp.names:
                continue
            ref = parse_id(imp.resolved_to.id)
            if ref.repo == self.ctx.repo and ref.path in self.file_index:
                found = self.exported(ref.path, name, imported, seen)
                if found:
                    imported.add(ref.path)
                    return found
        return []

    def class_method(
        self, class_name: str, method: str, prefer: list[str]
    ) -> tuple[Symbol | None, str]:
        """The method `class_name.method`, looking in `prefer` files first, then anywhere unique."""
        candidates: list[Symbol] = []
        for p in prefer:
            candidates += [
                s for s in self.file_index.get(p, {}).get(class_name, []) if s.kind == "class"
            ]
        conf = "exact"
        if not candidates:
            candidates = [s for s in self.global_index.get(class_name, []) if s.kind == "class"]
            conf = "heuristic"
            if len(candidates) != 1:
                return None, conf
        for cls in candidates:
            path = parse_id(cls.id).path or ""
            m = self.by_qname.get(path, {}).get(f"{cls.qualified_name}.{method}")
            if m:
                return m, conf
        return None, conf


def _aliases(f: FileEntry, idx: _Index) -> dict[str, set[str]]:
    """Receiver/alias name -> files it refers to (module aliases, package names, imported names)."""
    out: dict[str, set[str]] = defaultdict(set)
    for imp in f.imports:
        t = imp.resolved_to
        if t is None:
            continue
        ref = parse_id(t.id)
        if ref.repo != idx.ctx.repo:
            continue
        files: set[str] = set()
        if t.kind == "file" and ref.path in idx.by_path:
            files = {ref.path}
        elif t.kind == "dir":
            files = set(idx.dir_files.get(ref.path or "", []))
        if not files:
            continue
        module = imp.module.strip(".")
        if module:
            out[module].update(files)
            out[module.rsplit(".", 1)[-1].rsplit("/", 1)[-1]].update(files)
        m = re.search(r"\bas\s+(\w+)", imp.raw)
        if m:
            out[m.group(1)].update(files)
        for n in imp.names:
            if n != "*":
                out[n].update(files)
    return out


def _receiver_types(f: FileEntry, idx: _Index) -> dict[tuple[str | None, str], str]:
    """(scope symbol id or None, variable) -> class name, from assignments and annotated params."""
    out: dict[tuple[str | None, str], str] = {}
    lines = idx.lines(f.path)
    for s in f.symbols:
        if s.kind in ("function", "method"):
            inner = (
                s.signature[s.signature.find("(") + 1 : s.signature.rfind(")")]
                if "(" in s.signature
                else ""
            )
            for m in PARAM_ANN_RE.finditer(inner):
                out[(s.id, m.group(1))] = m.group(2).rsplit(".", 1)[-1]
    for i, line in enumerate(lines, start=1):
        m = ASSIGN_RE.match(line)
        if not m:
            continue
        var, ann, callee = m.group(1), m.group(2), m.group(3)
        typ = (ann or callee).rsplit(".", 1)[-1]
        if not typ[:1].isupper():
            continue
        scope = enclosing_symbol(f.symbols, i)
        out[(scope.id if scope else None, var)] = typ
    return out


def _locals(sym: Symbol, lines: list[str]) -> set[str]:
    names: set[str] = set()
    if "(" in sym.signature:
        inner = sym.signature[sym.signature.find("(") + 1 : sym.signature.rfind(")")]
        for part in inner.split(","):
            name = re.split(r"[:=\s*]", part.strip().lstrip("*&"), maxsplit=1)[0]
            if name:
                names.add(name)
    for line in lines[sym.start_line : sym.end_line]:
        m = LOCAL_ASSIGN_RE.match(line)
        if m:
            names.add(m.group(1))
    return names


def link(ctx: ScanContext, scan: LocalScan) -> None:
    files = scan.files
    idx = _Index(ctx, scan)
    edges: dict[tuple[str, str, str], Edge] = {}
    externals: set[str] = set()
    envs: set[str] = set()

    def add(
        source: str, target: str, kind: str, conf: str, line: int | None, note: str | None = None
    ) -> None:
        key = (source, target, kind)
        if key not in edges and source != target:
            edges[key] = Edge(
                source=source,
                target=target,
                kind=kind,
                confidence=conf,  # type: ignore[arg-type]
                line=line,
                note=note,
            )

    imported_files: dict[str, set[str]] = defaultdict(set)
    imported_names: dict[str, set[str]] = defaultdict(set)

    # 1. import / run / include / reference edges
    for f in files:
        for imp in f.imports:
            t = imp.resolved_to
            if t is None:
                continue
            if t.kind == "external":
                if not imp.is_stdlib:
                    externals.add(t.id[2:])
                    add(f.id, t.id, imp.edge_kind, "exact", imp.line)
                continue
            add(f.id, t.id, imp.edge_kind, "exact", imp.line)
            ref = parse_id(t.id)
            if ref.repo != ctx.repo:
                continue
            if t.kind == "file" and ref.path in idx.by_path:
                imported_files[f.path].add(ref.path)
                imported_names[f.path].update(imp.names)
                for name in imp.names:
                    for s in idx.exported(ref.path, name, imported_files[f.path]):
                        add(f.id, s.id, "imports", "exact", imp.line)
            elif t.kind == "dir":
                imported_files[f.path].update(idx.dir_files.get(ref.path or "", []))
            elif t.kind == "symbol":
                imported_files[f.path].add(ref.path or "")

    # 2. references: calls, inherits, handlers, env keys, tables
    tables: dict[str, Symbol] = {}
    for f in files:
        if f.language == "sql":
            for s in f.symbols:
                if s.kind in ("table", "view"):
                    tables.setdefault(s.name, s)

    for f in files:
        if not f.references:
            continue
        aliases = _aliases(f, idx)
        receivers = _receiver_types(f, idx) if any(r.receiver for r in f.references) else {}
        locals_cache: dict[str, set[str]] = {}
        by_start = {s.start_line: s for s in f.symbols if s.kind in ("route", "command")}
        count = 0
        for ref in f.references:
            if count >= MAX_EDGES_PER_FILE:
                break
            src = enclosing_symbol(
                [s for s in f.symbols if s.kind not in ("route", "command")], ref.line
            )
            source = src.id if src else f.id

            if ref.kind == "env":
                envs.add(ref.name)
                add(source, env_id(ref.name), "references", "exact", ref.line)
                continue
            if ref.kind == "env_define":
                envs.add(ref.name)
                add(f.id, env_id(ref.name), "defines", "exact", ref.line)
                continue
            if ref.kind == "table":
                if ref.name in tables:
                    add(source, tables[ref.name].id, "references", "exact", ref.line)
                    count += 1
                continue
            if ref.kind == "other":
                if f.language == "sql" and ref.name in tables:
                    add(f.id, tables[ref.name].id, "references", "exact", ref.line)
                continue
            if ref.kind == "handler":
                owner = by_start.get(ref.line)
                if owner is None:
                    continue
                target = _find_symbol(idx, f, ref.name, imported_files[f.path], exclude=owner.id)
                if target:
                    add(owner.id, target[0].id, "handles", target[1], ref.line)
                    count += 1
                continue

            kind = "inherits" if ref.kind == "class" else "calls"
            if ref.receiver:
                resolved = _resolve_receiver(
                    idx, f, ref, src, aliases, receivers, imported_files[f.path]
                )
                if resolved:
                    add(source, resolved[0].id, kind, resolved[1], ref.line)
                    count += 1
                    continue
            elif src is not None and src.kind in ("function", "method"):
                if src.id not in locals_cache:
                    locals_cache[src.id] = _locals(src, idx.lines(f.path))
                if ref.name in locals_cache[src.id]:
                    continue  # a parameter or local variable shadows the name

            local = [s for s in idx.file_index[f.path].get(ref.name, []) if s.id != source]
            if local:
                if len(local) == 1:
                    add(source, local[0].id, kind, "exact", ref.line)
                    count += 1
                continue
            imported = [
                s
                for p in imported_files.get(f.path, ())
                for s in idx.file_index.get(p, {}).get(ref.name, [])
            ]
            if ref.receiver:
                imported = [s for s in imported if s.parent is not None] or imported
            if imported:
                conf = (
                    "exact"
                    if (ref.name in imported_names[f.path] or len(imported) == 1)
                    else "heuristic"
                )
                if ref.receiver and ref.name not in imported_names[f.path]:
                    conf = "heuristic"
                if len(imported) <= MAX_AMBIGUOUS:
                    for s in imported:
                        add(source, s.id, kind, conf, ref.line)
                        count += 1
                continue
            candidates = [
                s
                for s in idx.global_index.get(ref.name, [])
                if not s.id.startswith(f"s:{ctx.repo}:{f.path}#")
            ]
            if len(candidates) == 1:
                add(source, candidates[0].id, kind, "heuristic", ref.line, "matched by name only")
                count += 1

    # 3. tests -> code
    for f in files:
        if "test" not in f.roles:
            continue
        for s in f.symbols:
            if s.kind not in ("function", "method", "class"):
                continue
            m = TEST_NAME_RE.match(s.name)
            if not m:
                continue
            cand = (m.group(1) or m.group(2) or "").lower().replace("_", "")
            if len(cand) < 3:
                continue
            pool = [
                t
                for p in imported_files.get(f.path, ())
                for t in idx.by_path[p].symbols
                if t.kind in ("function", "method", "class")
            ]
            hits = [t for t in pool if t.name.lower().replace("_", "") == cand]
            conf = "exact"
            if not hits:
                globals_ = [
                    t
                    for name, lst in idx.global_index.items()
                    if name.lower().replace("_", "") == cand
                    for t in lst
                    if "test" not in idx.by_path[parse_id(t.id).path or ""].roles
                    and t.kind in ("function", "method", "class")
                ]
                hits = globals_ if len(globals_) == 1 else []
                conf = "heuristic"
            for t in hits[:MAX_AMBIGUOUS]:
                add(s.id, t.id, "tests", conf, s.start_line)

    scan.edges = sorted(edges.values(), key=Edge.key)
    scan.externals = sorted(externals)
    scan.envs = sorted(envs)

    # 4. file metrics from edges
    fan_in: dict[str, set[str]] = defaultdict(set)
    fan_out: dict[str, set[str]] = defaultdict(set)
    for e in scan.edges:
        sp, tp = parse_id(e.source), parse_id(e.target)
        if sp.path and tp.path and sp.path != tp.path and sp.repo == tp.repo == ctx.repo:
            fan_out[sp.path].add(tp.path)
            fan_in[tp.path].add(sp.path)
    for f in files:
        f.metrics = {
            "symbols": len(f.symbols),
            "fan_in": len(fan_in.get(f.path, ())),
            "fan_out": len(fan_out.get(f.path, ())),
        }


def _find_symbol(
    idx: _Index, f: FileEntry, name: str, imported: set[str], exclude: str
) -> tuple[Symbol, str] | None:
    local = [s for s in idx.file_index[f.path].get(name, []) if s.id != exclude]
    if len(local) == 1:
        return local[0], "exact"
    imp = [s for p in imported for s in idx.file_index.get(p, {}).get(name, [])]
    if len(imp) == 1:
        return imp[0], "exact"
    glob = [s for s in idx.global_index.get(name, []) if s.kind in ("function", "method", "class")]
    if len(glob) == 1:
        return glob[0], "heuristic"
    return None


def _resolve_receiver(
    idx: _Index, f: FileEntry, ref, src: Symbol | None, aliases, receivers, imported: set[str]
) -> tuple[Symbol, str] | None:
    r = ref.receiver
    if r in SELF_NAMES:
        cls = None
        if src is not None:
            if src.kind == "class":
                cls = src
            elif src.parent:
                parent = next((s for s in f.symbols if s.id == src.parent), None)
                cls = parent if parent and parent.kind == "class" else None
        if cls is not None:
            m = idx.by_qname[f.path].get(f"{cls.qualified_name}.{ref.name}")
            if m:
                return m, "exact"
        return None
    if r in aliases:
        found: list[Symbol] = []
        for p in sorted(aliases[r]):
            found += idx.exported(p, ref.name, imported)
        if len(found) == 1:
            return found[0], "exact"
        if 1 < len(found) <= MAX_AMBIGUOUS:
            return found[0], "heuristic"
        return None
    typ = receivers.get((src.id if src else None, r)) or receivers.get((None, r))
    if typ:
        prefer = [f.path, *sorted(imported)]
        m, conf = idx.class_method(typ, ref.name, prefer)
        if m:
            return m, conf
    return None
