"""Author documentation: the comment block above a definition, or the file header, for any language."""

from __future__ import annotations

import re

from metis.model import Symbol

LINE_PREFIX = {
    "python": "#",
    "bash": "#",
    "yaml": "#",
    "ruby": "#",
    "toml": "#",
    "make": "#",
    "dockerfile": "#",
    "ini": "#",
    "hcl": "#",
    "sql": "--",
    "lua": "--",
}
BLOCK_LANGS = {
    "javascript",
    "typescript",
    "tsx",
    "java",
    "kotlin",
    "c",
    "cpp",
    "csharp",
    "go",
    "rust",
    "php",
    "swift",
    "scala",
    "css",
    "scss",
}
SKIP_ABOVE = re.compile(r"^\s*(@|#\[|\[[A-Z])")  # decorators and attributes between doc and def
TAG_LINE = re.compile(r"^\s*(@\w+|:\w+:|:param|:return|:rtype|:raises|Args:|Returns:|Raises:)")
LICENSE = re.compile(r"copyright|licen[cs]e|all rights reserved|SPDX", re.I)
MAX_DOC = 2000
PY_MODULE_DOC = re.compile(r'^(?:#![^\n]*\n|#.*coding[^\n]*\n|\s)*("""|\'\'\')(.*?)\1', re.S)


def _prefix(lang: str | None) -> str:
    return LINE_PREFIX.get(lang or "", "//")


def _is_line_comment(line: str, prefix: str) -> bool:
    t = line.strip()
    if not t.startswith(prefix):
        return False
    return not (prefix == "#" and t.startswith("#!")) and not t.startswith(prefix + "!")


def _clean(lines: list[str]) -> str | None:
    out: list[str] = []
    for raw in lines:
        t = raw.strip()
        for marker in ("/**", "/*!", "/*", "*/", "<!--", "-->"):
            t = t.replace(marker, " ")
        t = re.sub(r"^(///|//!|//|#!|#|--|\*|;)\s?", "", t.strip()).rstrip()
        if TAG_LINE.match(t):
            continue
        out.append(t)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    text = "\n".join(out).strip()
    if not text or len(text) < 3:
        return None
    if len(text) > MAX_DOC:
        text = text[:MAX_DOC].rstrip() + "..."
    return text


def _block_above(lines: list[str], idx: int, lang: str | None) -> list[str]:
    """Comment lines immediately above line index `idx` (exclusive), in file order."""
    prefix = _prefix(lang)
    i = idx - 1
    while i >= 0 and SKIP_ABOVE.match(lines[i]):
        i -= 1
    if i < 0:
        return []
    if lang in BLOCK_LANGS and lines[i].strip().endswith("*/"):
        block: list[str] = []
        while i >= 0:
            block.append(lines[i])
            if "/*" in lines[i]:
                break
            i -= 1
        return list(reversed(block))
    block = []
    while i >= 0 and _is_line_comment(lines[i], prefix):
        block.append(lines[i])
        i -= 1
    return list(reversed(block))


def _header(lines: list[str], lang: str | None, text: str, symbols: list[Symbol]) -> str | None:
    if lang == "python":
        m = PY_MODULE_DOC.match(text)
        if m:
            return _clean(m.group(2).splitlines())
    prefix = _prefix(lang)
    i = 0
    while (
        i < len(lines)
        and lines[i].startswith("#!")
        or (i < len(lines) and lines[i].startswith("#") and "coding" in lines[i][:40])
    ):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return None
    block: list[str] = []
    first = lines[i].strip()
    last = i
    if lang in BLOCK_LANGS and first.startswith("/*"):
        while i < len(lines):
            block.append(lines[i])
            if "*/" in lines[i]:
                break
            i += 1
        last = i
    elif _is_line_comment(lines[i], prefix):
        while i < len(lines) and _is_line_comment(lines[i], prefix):
            block.append(lines[i])
            i += 1
        last = i - 1
    elif lang in ("html", "xml", "markdown") and first.startswith("<!--"):
        while i < len(lines):
            block.append(lines[i])
            if "-->" in lines[i]:
                break
            i += 1
        last = i
    if not block:
        return None
    # a comment directly above the first definition documents that definition, not the file
    nxt = last + 1
    if nxt < len(lines) and lines[nxt].strip() and any(s.start_line == nxt + 1 for s in symbols):
        return None
    doc = _clean(block)
    if (
        doc
        and LICENSE.search(doc)
        and len(doc.splitlines()) <= 8
        and not re.search(r"\.\s", doc.split("\n")[0])
    ):
        return None  # a bare licence header is not documentation
    return doc


def extract_docs(
    lang: str | None, text: str, symbols: list[Symbol]
) -> tuple[str | None, dict[str, str]]:
    """Return (header_doc, {symbol_id: doc}) for symbols that have no docstring yet."""
    lines = text.splitlines()
    header = _header(lines, lang, text, symbols)
    docs: dict[str, str] = {}
    for s in symbols:
        if s.docstring or s.kind in ("key", "route", "command"):
            continue
        idx = s.start_line - 1
        if idx <= 0 or idx > len(lines):
            continue
        block = _block_above(lines, idx, lang)
        if not block:
            continue
        doc = _clean(block)
        if doc and doc != header:
            docs[s.id] = doc
    return header, docs
