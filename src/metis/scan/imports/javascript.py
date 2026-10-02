from __future__ import annotations

import posixpath

from tree_sitter import Node, Tree

from metis.model import Import, ResolvedTarget, external_id
from metis.scan.imports.base import Extraction, LanguageHandler, ScanContext, relative_candidates
from metis.scan.parsers import make_query, node_text, run_query, unquote

EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".json", ".vue", ".svelte"]
NAME_TYPES = {"identifier", "shorthand_property_identifier_pattern", "property_identifier"}

QUERY = """
(import_statement source: (string) @src) @imp
(export_statement source: (string) @src) @imp
(call_expression function: (identifier) @fn arguments: (arguments . (string) @src)) @imp
(call_expression function: (import) arguments: (arguments . (string) @src)) @imp
"""


def _names(node: Node) -> list[str]:
    out: list[str] = []
    stack = list(node.named_children)
    while stack:
        n = stack.pop()
        if n.type in NAME_TYPES:
            out.append(node_text(n))
        stack.extend(n.named_children)
    return sorted(set(out))


class JavaScriptHandler(LanguageHandler):
    languages = ("javascript", "typescript", "tsx")

    def extract(self, ctx: ScanContext, path: str, tree: Tree | None, data: bytes) -> Extraction:
        if tree is None:
            return Extraction()
        lang = (
            "tsx"
            if path.endswith(".tsx")
            else "typescript"
            if path.endswith((".ts", ".mts", ".cts"))
            else "javascript"
        )
        query = make_query(lang, QUERY)
        if query is None:
            return Extraction()
        imports: list[Import] = []
        seen: set[tuple[int, str]] = set()
        for _p, caps in run_query(query, tree.root_node):
            node = caps["imp"][0]
            fn = caps.get("fn")
            if fn and node_text(fn[0]) not in ("require", "import"):
                continue
            src = unquote(node_text(caps["src"][0]))
            line = node.start_point.row + 1
            if (line, src) in seen:
                continue
            seen.add((line, src))
            names: list[str] = []
            if node.type == "import_statement":
                clause = next((c for c in node.named_children if c.type == "import_clause"), None)
                if clause is not None:
                    names = _names(clause)
            elif node.type == "call_expression" and node.parent is not None:
                decl = node.parent
                if decl.type == "variable_declarator":
                    name_node = decl.child_by_field_name("name")
                    if name_node is not None:
                        names = (
                            [node_text(name_node)]
                            if name_node.type == "identifier"
                            else _names(name_node)
                        )
            raw = node_text(node).split("\n", 1)[0].strip()
            imp = Import(raw=raw, module=src, names=names, line=line)
            imp.resolved_to = self.resolve(ctx, path, imp)
            imports.append(imp)
        return Extraction(imports=imports)

    def resolve(self, ctx: ScanContext, path: str, imp: Import) -> ResolvedTarget | None:
        src = imp.module
        if src.startswith("."):
            base = relative_candidates(path, src)
            return ctx.first_existing(
                [base + e for e in EXTS]
                + [posixpath.join(base, "index" + e) for e in EXTS]
                + [base]
            )
        if src.startswith("/"):
            return ctx.first_existing([src.lstrip("/")])
        if src.startswith(("@/", "~/")):  # common aliases for src/
            rest = src[2:]
            return ctx.first_existing(
                [posixpath.join(p, rest) + e for p in ("src", "") for e in [""] + EXTS]
            )
        for alias, target in ctx.ts_paths.items():
            prefix = alias[:-1] if alias.endswith("*") else alias
            if src == prefix.rstrip("/") or (alias.endswith("*") and src.startswith(prefix)):
                rest = src[len(prefix) :]
                base = (target[:-1] if target.endswith("*") else target) + rest
                base = posixpath.normpath(base)
                found = ctx.first_existing(
                    [base + e for e in EXTS]
                    + [posixpath.join(base, "index" + e) for e in EXTS]
                    + [base]
                )
                if found is not None:
                    return found
        parts = src.split("/")
        pkg = "/".join(parts[:2]) if src.startswith("@") else parts[0]
        for other in ctx.other_repos:
            if pkg in other.package_names:
                return ctx.other_repo_target(other, None)
        return ResolvedTarget(kind="external", id=external_id(pkg))
