from __future__ import annotations

from tree_sitter import Tree

from metis.model import Import, ResolvedTarget, external_id
from metis.scan.imports.base import Extraction, LanguageHandler, ScanContext
from metis.scan.parsers import make_query, node_text, run_query, unquote

QUERY = "(import_spec path: (interpreted_string_literal) @path) @spec"


class GoHandler(LanguageHandler):
    languages = ("go",)

    def extract(self, ctx: ScanContext, path: str, tree: Tree | None, data: bytes) -> Extraction:
        if tree is None:
            return Extraction()
        query = make_query("go", QUERY)
        if query is None:
            return Extraction()
        imports: list[Import] = []
        for _p, caps in run_query(query, tree.root_node):
            spec = caps["spec"][0]
            module = unquote(node_text(caps["path"][0]))
            imp = Import(raw=node_text(spec).strip(), module=module, line=spec.start_point.row + 1)
            imp.resolved_to = self.resolve(ctx, path, imp)
            imports.append(imp)
        return Extraction(imports=imports)

    def resolve(self, ctx: ScanContext, path: str, imp: Import) -> ResolvedTarget | None:
        mod = imp.module
        if ctx.go_module and (mod == ctx.go_module or mod.startswith(ctx.go_module + "/")):
            rel = mod[len(ctx.go_module) :].strip("/")
            return ctx.target_for_path(rel) if rel else ctx.target_for_path("")
        for other in ctx.other_repos:
            gm = other.go_module
            if gm and (mod == gm or mod.startswith(gm + "/")):
                return ctx.other_repo_target(other, mod[len(gm) :].strip("/") or None)
        parts = mod.split("/")
        if "." in parts[0] and len(parts) >= 3:
            name = "/".join(parts[:3])
        else:
            name = parts[0]
        imp.is_stdlib = "." not in parts[0]
        return ResolvedTarget(kind="external", id=external_id(name))
