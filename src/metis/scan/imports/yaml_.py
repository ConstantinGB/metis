"""YAML: keys become symbols, path-like values and run commands become edges."""

from __future__ import annotations

import posixpath
import re

import yaml
from tree_sitter import Tree

from metis.model import Import, ResolvedTarget, Symbol, symbol_id
from metis.scan.imports.base import Extraction, LanguageHandler, ScanContext, relative_candidates

RUN_KEYS = {
    "run",
    "script",
    "command",
    "entrypoint",
    "cmd",
    "before_script",
    "after_script",
    "steps",
    "commands",
    "exec",
}
TOKEN_SPLIT = re.compile(r"[\s;&|()<>]+")


def _clean(token: str) -> str:
    token = token.strip().strip("\"'`")
    if token.startswith("./"):
        token = token[2:]
    return token


class YamlHandler(LanguageHandler):
    languages = ("yaml",)

    def extract(self, ctx: ScanContext, path: str, tree: Tree | None, data: bytes) -> Extraction:
        text = data.decode("utf-8", errors="replace")
        try:
            docs = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
        except yaml.YAMLError:
            return Extraction(imports=[], symbols=[], references=[])
        symbols: list[Symbol] = []
        imports: list[Import] = []
        seen: set[tuple[str, int]] = set()

        def add_import(value: str, line: int, kind: str) -> None:
            cand = _clean(value)
            if not cand or (cand, line) in seen:
                return
            target = self._resolve(ctx, path, cand, allow_dir=(kind != "runs"))
            if target is None:
                return
            seen.add((cand, line))
            imports.append(
                Import(
                    raw=value.strip()[:120],
                    module=cand,
                    line=line,
                    resolved_to=target,
                    edge_kind=kind,
                )
            )  # type: ignore[arg-type]

        def walk(node: yaml.Node, key: str | None, in_run: bool) -> None:
            if isinstance(node, yaml.MappingNode):
                for k, v in node.value:
                    kname = k.value if isinstance(k, yaml.ScalarNode) else None
                    walk(v, kname, in_run or (kname in RUN_KEYS))
            elif isinstance(node, yaml.SequenceNode):
                for item in node.value:
                    walk(item, key, in_run)
            elif isinstance(node, yaml.ScalarNode):
                if not isinstance(node.value, str) or not node.value.strip():
                    return
                line = node.start_mark.line + 1
                value = node.value
                if in_run or key in RUN_KEYS:
                    for i, raw_line in enumerate(value.splitlines()):
                        for tok in TOKEN_SPLIT.split(raw_line):
                            if "/" in tok or "." in tok:
                                add_import(tok, line + i, "runs")
                elif "\n" not in value and len(value) < 300:
                    add_import(
                        value.split(":", 1)[0]
                        if ":" in value and not value.startswith(("http", "@"))
                        else value,
                        line,
                        "references",
                    )

        for doc in docs:
            if isinstance(doc, yaml.MappingNode):
                for k, v in doc.value:
                    if isinstance(k, yaml.ScalarNode):
                        name = str(k.value)
                        symbols.append(
                            Symbol(
                                id=symbol_id(ctx.repo, path, name),
                                kind="key",
                                name=name,
                                qualified_name=name,
                                start_line=k.start_mark.line + 1,
                                end_line=max(v.end_mark.line, k.start_mark.line + 1),
                                signature=f"{name}:",
                            )
                        )
            if doc is not None:
                walk(doc, None, False)
        return Extraction(imports=imports, symbols=symbols, references=[])

    @staticmethod
    def _resolve(ctx: ScanContext, path: str, cand: str, allow_dir: bool) -> ResolvedTarget | None:
        if cand.startswith(("http://", "https://", "$", "{")) or cand in ("", ".", "/"):
            return None
        for rel in (relative_candidates(path, cand), posixpath.normpath(cand)):
            t = ctx.target_for_path(rel)
            if t is not None and (allow_dir or t.kind == "file"):
                if t.kind == "dir" and "/" not in cand:
                    continue  # bare words like "build" matching a directory are noise
                return t
        return None
