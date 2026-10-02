"""Generic symbol and reference extraction from tree-sitter tags queries."""

from __future__ import annotations

import re

from tree_sitter import Node, Tree

from metis.model import Reference, Symbol, symbol_id
from metis.scan.parsers import get_tags_query, node_text, run_query

KIND_MAP = {
    "function": "function",
    "method": "method",
    "class": "class",
    "module": "module",
    "interface": "interface",
    "type": "type",
    "constant": "constant",
    "variable": "variable",
    "enum": "type",
    "struct": "type",
    "macro": "other",
    "namespace": "module",
    "union": "type",
    "trait": "interface",
    "impl": "class",
}
SKIP_KINDS = {"field", "property", "parameter", "label"}
SIGNATURE_MAX = 160
BRANCH_RE = re.compile(
    r"\b(if|elif|else if|for|while|case|when|catch|except|rescue|match)\b|&&|\|\||\?\s"
)
RECEIVER_FIELDS = ("object", "operand", "expression", "value", "receiver")


def _signature(node: Node) -> str:
    first = node_text(node).split("\n", 1)[0].strip()
    if len(first) > SIGNATURE_MAX:
        first = first[:SIGNATURE_MAX] + "..."
    return first


def _python_docstring(node: Node) -> str | None:
    body = node.child_by_field_name("body")
    if body is None or not body.named_children:
        return None
    first = body.named_children[0]
    if first.type == "expression_statement" and first.named_children:
        first = first.named_children[0]
    if first.type != "string":
        return None
    text = node_text(first).strip()
    for q in ('"""', "'''", '"', "'"):
        if text.startswith(q) and text.endswith(q) and len(text) >= 2 * len(q):
            text = text[len(q) : -len(q)]
            break
    return text.strip() or None


def _python_module_assignments(root: Node) -> list[tuple[str, str, Node]]:
    """Module-level `NAME = ...` statements: constants when upper-case, variables otherwise."""
    out: list[tuple[str, str, Node]] = []
    for stmt in root.named_children:
        assign = stmt
        if stmt.type == "expression_statement" and stmt.named_children:
            assign = stmt.named_children[0]
        if assign.type != "assignment":
            continue
        left = assign.child_by_field_name("left")
        if left is None or left.type != "identifier":
            continue
        name = node_text(left)
        out.append(("constant" if name.isupper() else "variable", name, stmt))
    return out


def _receiver(call: Node) -> str | None:
    """`obj` for `obj.name(...)`, dotted text for `a.b.name(...)`, None for plain calls."""
    fn = call.child_by_field_name("function")
    if fn is None:
        fn = next((c for c in call.named_children), None)
    if fn is None or fn.type in ("identifier", "field_identifier"):
        return None
    for field in RECEIVER_FIELDS:
        obj = fn.child_by_field_name(field)
        if obj is not None:
            text = node_text(obj)
            if obj.type in ("identifier", "this", "self") or re.fullmatch(r"[\w.]+", text):
                return text[:80]
            return None
    return None


def _metrics(node: Node) -> dict[str, int]:
    text = node_text(node)
    return {
        "lines": node.end_point.row - node.start_point.row + 1,
        "branches": len(BRANCH_RE.findall(text)),
    }


def extract_symbols(
    repo: str, path: str, lang: str, tree: Tree
) -> tuple[list[Symbol], list[Reference]]:
    query = get_tags_query(lang)
    if query is None:
        return [], []

    raw_defs: list[tuple[str, str, Node]] = []
    refs: list[Reference] = []
    for _pattern, caps in run_query(query, tree.root_node):
        name_nodes = caps.get("name")
        if not name_nodes:
            continue
        name = node_text(name_nodes[0])
        for cap, nodes in caps.items():
            if cap.startswith("definition."):
                tag = cap.split(".", 1)[1]
                if tag in SKIP_KINDS:
                    continue
                raw_defs.append((KIND_MAP.get(tag, "other"), name, nodes[0]))
            elif cap.startswith("reference."):
                tag = cap.split(".", 1)[1]
                rkind = "call" if tag == "call" else "class" if tag == "class" else "other"
                refs.append(
                    Reference(
                        name=name,
                        line=name_nodes[0].start_point.row + 1,
                        kind=rkind,
                        col=name_nodes[0].start_point.column,
                        receiver=_receiver(nodes[0]) if tag == "call" else None,
                    )
                )

    if lang == "python":
        raw_defs.extend(_python_module_assignments(tree.root_node))

    # Nesting by byte containment
    raw_defs.sort(key=lambda d: (d[2].start_byte, -d[2].end_byte))
    symbols: list[Symbol] = []
    stack: list[tuple[Node, Symbol]] = []
    seen: set[str] = set()
    for kind, name, node in raw_defs:
        while stack and stack[-1][0].end_byte <= node.start_byte:
            stack.pop()
        parent = stack[-1][1] if stack else None
        if parent is not None and parent.kind == "class" and kind == "function":
            kind = "method"
        qname = f"{parent.qualified_name}.{name}" if parent else name
        sid = symbol_id(repo, path, qname)
        if sid in seen:
            qname = f"{qname}~{node.start_point.row + 1}"
            sid = symbol_id(repo, path, qname)
        seen.add(sid)
        sym = Symbol(
            id=sid,
            kind=kind,  # type: ignore[arg-type]
            name=name,
            qualified_name=qname,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            signature=_signature(node),
            docstring=_python_docstring(node) if lang == "python" else None,
            parent=parent.id if parent else None,
            metrics=_metrics(node) if kind in ("function", "method", "class") else {},
        )
        symbols.append(sym)
        stack.append((node, sym))

    def_lines = {(s.name, s.start_line) for s in symbols}
    refs = [r for r in refs if (r.name, r.line) not in def_lines]
    refs.sort(key=lambda r: (r.line, r.col, r.name))
    return symbols, refs


def enclosing_symbol(symbols: list[Symbol], line: int) -> Symbol | None:
    best: Symbol | None = None
    for s in symbols:
        if s.start_line <= line <= s.end_line:
            if best is None or (s.end_line - s.start_line) < (best.end_line - best.start_line):
                best = s
    return best
