from __future__ import annotations

import posixpath
import sys

from tree_sitter import Node, Tree

from metis.model import Import, ResolvedTarget, external_id
from metis.scan.imports.base import Extraction, LanguageHandler, ScanContext
from metis.scan.parsers import make_query, node_text, run_query

STDLIB = set(sys.stdlib_module_names) | {"__future__"}

QUERY = """
(import_statement) @imp
(import_from_statement) @imp
"""


def _dotted(node: Node | None) -> str:
    return node_text(node) if node is not None else ""


class PythonHandler(LanguageHandler):
    languages = ("python",)

    def extract(self, ctx: ScanContext, path: str, tree: Tree | None, data: bytes) -> Extraction:
        if tree is None:
            return Extraction()
        query = make_query("python", QUERY)
        if query is None:
            return Extraction()
        imports: list[Import] = []
        for _p, caps in run_query(query, tree.root_node):
            node = caps["imp"][0]
            line = node.start_point.row + 1
            raw = node_text(node).split("\n", 1)[0].strip()
            if node.type == "import_statement":
                for child in node.named_children:
                    if child.type == "dotted_name":
                        imports.append(Import(raw=raw, module=node_text(child), line=line))
                    elif child.type == "aliased_import":
                        name = child.child_by_field_name("name")
                        imports.append(Import(raw=raw, module=_dotted(name), line=line))
            else:
                mod_node = node.child_by_field_name("module_name")
                module = _dotted(mod_node)
                names: list[str] = []
                for n in node.children_by_field_name("name"):
                    if n.type == "aliased_import":
                        names.append(_dotted(n.child_by_field_name("name")))
                    else:
                        names.append(node_text(n))
                if any(c.type == "wildcard_import" for c in node.named_children):
                    names.append("*")
                imports.append(Import(raw=raw, module=module, names=names, line=line))
        return Extraction(imports=self._resolve_all(ctx, path, imports))

    # -- resolution -----------------------------------------------------------

    def _resolve_all(self, ctx: ScanContext, path: str, imports: list[Import]) -> list[Import]:
        out: list[Import] = []
        for imp in imports:
            module = imp.module
            dots = len(module) - len(module.lstrip("."))
            rest = module[dots:]
            if dots:
                base = posixpath.dirname(path)
                for _ in range(dots - 1):
                    base = posixpath.dirname(base)
                segs = [s for s in rest.split(".") if s]
                pkg_dir = posixpath.join(base, *segs) if segs else base
                target = ctx.first_existing(
                    [pkg_dir + ".py", posixpath.join(pkg_dir, "__init__.py")]
                )
                out.extend(self._split_submodules(ctx, imp, target, pkg_dir))
                continue

            segs = rest.split(".")
            first = segs[0]
            if first in STDLIB:
                imp.is_stdlib = True
                imp.resolved_to = ResolvedTarget(kind="external", id=external_id(first))
                out.append(imp)
                continue

            roots = ctx.python_package_roots()
            prefixes = [roots[first]] if first in roots else ["", "src", "lib"]
            candidates: list[str] = []
            for prefix in prefixes:
                p = posixpath.join(prefix, *segs) if prefix else "/".join(segs)
                candidates += [p + ".py", posixpath.join(p, "__init__.py")]
            target = ctx.first_existing(candidates)
            if target is not None:
                pkg_dir = (
                    posixpath.dirname(target.id.split(":", 2)[2])
                    if target.id.endswith("__init__.py")
                    else None
                )
                out.extend(self._split_submodules(ctx, imp, target, pkg_dir))
                continue

            other = self._other_repo(ctx, first)
            if other is not None:
                oroots = other.python_package_roots()
                prefix = oroots.get(first, "")
                p = posixpath.join(prefix, *segs) if prefix else "/".join(segs)
                files = other.files()
                rel = next((c for c in (p + ".py", p + "/__init__.py") if c in files), None)
                imp.resolved_to = ctx.other_repo_target(other, rel or (p if p else None))
                out.append(imp)
                continue

            imp.resolved_to = ResolvedTarget(kind="external", id=external_id(first))
            out.append(imp)
        return out

    @staticmethod
    def _other_repo(ctx: ScanContext, first: str):
        for other in ctx.other_repos:
            names = {n.replace("-", "_") for n in other.package_names}
            if first in names or first in other.python_package_roots():
                return other
        return None

    @staticmethod
    def _split_submodules(
        ctx: ScanContext, imp: Import, target: ResolvedTarget | None, pkg_dir: str | None
    ) -> list[Import]:
        """`from pkg import a, b` where a is a submodule -> separate resolved imports."""
        out: list[Import] = []
        remaining: list[str] = []
        if pkg_dir is not None and imp.names:
            for name in imp.names:
                sub = ctx.first_existing(
                    [
                        posixpath.join(pkg_dir, name + ".py"),
                        posixpath.join(pkg_dir, name, "__init__.py"),
                    ]
                )
                if sub is not None:
                    out.append(
                        Import(
                            raw=imp.raw,
                            module=f"{imp.module}.{name}".replace("..", ".."),
                            names=[],
                            line=imp.line,
                            resolved_to=sub,
                        )
                    )
                else:
                    remaining.append(name)
        else:
            remaining = list(imp.names)
        if target is not None or not out:
            imp.names = remaining
            imp.resolved_to = target
            out.append(imp)
        return out
