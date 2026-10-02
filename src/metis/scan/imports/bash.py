"""Bash: `source` becomes an include edge; invoking a repo file becomes a run edge."""

from __future__ import annotations

import posixpath

from tree_sitter import Node, Tree

from metis.model import Import
from metis.scan.imports.base import Extraction, LanguageHandler, ScanContext, relative_candidates
from metis.scan.parsers import node_text

ARG_TYPES = {"word", "string", "raw_string", "concatenation", "number"}


def _clean(tok: str) -> str:
    tok = tok.strip().strip("\"'")
    if tok.startswith("./"):
        tok = tok[2:]
    return tok


class BashHandler(LanguageHandler):
    languages = ("bash",)

    def extract(self, ctx: ScanContext, path: str, tree: Tree | None, data: bytes) -> Extraction:
        if tree is None:
            return Extraction()
        imports: list[Import] = []
        seen: set[tuple[str, int]] = set()
        stack: list[Node] = [tree.root_node]
        while stack:
            node = stack.pop()
            stack.extend(node.named_children)
            if node.type != "command":
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(name_node)
            args = [
                node_text(c)
                for c in node.named_children
                if c.type in ARG_TYPES and c is not name_node
            ]
            line = node.start_point.row + 1
            raw = node_text(node).split("\n", 1)[0].strip()[:120]
            if name in ("source", ".") and args:
                cand = _clean(args[0])
                kind = "includes"
                tokens = [cand]
            else:
                kind = "runs"
                tokens = [_clean(t) for t in [name, *args]]
            for tok in tokens:
                if not tok or "$" in tok or (tok, line) in seen:
                    continue
                target = None
                for rel in (relative_candidates(path, tok), posixpath.normpath(tok)):
                    t = ctx.target_for_path(rel)
                    if t is not None and t.kind == "file":
                        target = t
                        break
                if target is None:
                    continue
                seen.add((tok, line))
                imports.append(
                    Import(raw=raw, module=tok, line=line, resolved_to=target, edge_kind=kind)
                )  # type: ignore[arg-type]
        return Extraction(imports=imports)
