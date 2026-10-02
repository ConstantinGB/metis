"""SQL (MySQL flavoured): CREATE statements become symbols, REFERENCES/SOURCE become edges."""

from __future__ import annotations

import posixpath
import re

from tree_sitter import Tree

from metis.model import Import, Reference, Symbol, symbol_id
from metis.scan.imports.base import Extraction, LanguageHandler, ScanContext, relative_candidates

CREATE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+)?(?:DEFINER\s*=\s*\S+\s+)?"
    r"(TABLE|VIEW|PROCEDURE|FUNCTION|TRIGGER|(?:UNIQUE\s+)?INDEX)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[`\"']?(\w+)[`\"']?",
    re.IGNORECASE,
)
REFERENCES = re.compile(r"\bREFERENCES\s+[`\"']?(\w+)[`\"']?", re.IGNORECASE)
SOURCE = re.compile(r"^\s*(?:SOURCE|\\\.)\s+(\S+)", re.IGNORECASE | re.MULTILINE)

KIND = {
    "table": "table",
    "view": "view",
    "procedure": "procedure",
    "function": "function",
    "trigger": "trigger",
    "index": "index",
}


class SqlHandler(LanguageHandler):
    languages = ("sql",)

    def extract(self, ctx: ScanContext, path: str, tree: Tree | None, data: bytes) -> Extraction:
        text = data.decode("utf-8", errors="replace")
        symbols: list[Symbol] = []
        seen: set[str] = set()
        for m in CREATE.finditer(text):
            kind_word = m.group(1).split()[-1].lower()
            name = m.group(2)
            start_line = text.count("\n", 0, m.start()) + 1
            end = text.find(";", m.end())
            end_line = text.count("\n", 0, end if end != -1 else len(text)) + 1
            qname = name if name not in seen else f"{name}~{start_line}"
            seen.add(name)
            signature = text[
                m.start() : text.find("\n", m.start()) if "\n" in text[m.start() :] else len(text)
            ]
            symbols.append(
                Symbol(
                    id=symbol_id(ctx.repo, path, qname),
                    kind=KIND.get(kind_word, "other"),  # type: ignore[arg-type]
                    name=name,
                    qualified_name=qname,
                    start_line=start_line,
                    end_line=end_line,
                    signature=signature.strip()[:160],
                )
            )
        references = [
            Reference(name=m.group(1), line=text.count("\n", 0, m.start()) + 1, kind="other")
            for m in REFERENCES.finditer(text)
        ]
        imports: list[Import] = []
        for m in SOURCE.finditer(text):
            cand = m.group(1).strip("'\"`;")
            line = text.count("\n", 0, m.start()) + 1
            target = None
            for rel in (relative_candidates(path, cand), posixpath.normpath(cand)):
                target = ctx.target_for_path(rel)
                if target is not None:
                    break
            imports.append(
                Import(
                    raw=m.group(0).strip(),
                    module=cand,
                    line=line,
                    resolved_to=target,
                    edge_kind="includes",
                )
            )
        return Extraction(imports=imports, symbols=symbols, references=references)
