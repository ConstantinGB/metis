"""tree-sitter parser and query cache."""

from __future__ import annotations

from functools import cache

import tree_sitter_language_pack as pack
from tree_sitter import Language, Node, Parser, Query, QueryCursor, QueryError, Tree

# Languages the pack ships no tags query for, but we can write a small one.
EXTRA_TAGS: dict[str, str] = {
    "bash": """
(function_definition name: (word) @name) @definition.function
(command name: (command_name (word) @name)) @reference.call
""",
}

# Additional patterns appended to the bundled query.
APPEND_TAGS: dict[str, str] = {
    "python": """
(class_definition
  superclasses: (argument_list
    [(identifier) @name (attribute attribute: (identifier) @name)])) @reference.class
""",
}

# The TypeScript queries only add TS-specific nodes; classes and functions live in the JS query.
INHERITS: dict[str, list[str]] = {"typescript": ["javascript"], "tsx": ["javascript"]}

# Patterns in bundled queries that do not match with this binding; we handle them in code.
DROP_PATTERNS: dict[str, list[str]] = {
    "python": [
        "(module (expression_statement (assignment left: (identifier) @name) @definition.constant))"
    ],
}

# Languages we never hand to tree-sitter (custom handlers or plain text).
NO_PARSE = {
    "sql",
    "yaml",
    "json",
    "toml",
    "markdown",
    "text",
    "ini",
    "xml",
    "html",
    "css",
    "scss",
    "hcl",
    "dockerfile",
    "make",
    "cmake",
    "groovy",
}


@cache
def get_language(lang: str) -> Language | None:
    try:
        return pack.get_language(lang)
    except Exception:
        return None


@cache
def get_parser(lang: str) -> Parser | None:
    try:
        return pack.get_parser(lang)
    except Exception:
        return None


def language_supported(lang: str | None) -> bool:
    return bool(lang) and lang not in NO_PARSE and get_parser(lang) is not None


def parse(lang: str, data: bytes) -> Tree | None:
    parser = get_parser(lang)
    if parser is None:
        return None
    try:
        return parser.parse(data)
    except Exception:
        return None


@cache
def make_query(lang: str, source: str) -> Query | None:
    language = get_language(lang)
    if language is None:
        return None
    try:
        return Query(language, source)
    except QueryError:
        return None


def _own_tags_source(lang: str) -> str:
    src = EXTRA_TAGS.get(lang)
    if src is None:
        try:
            src = pack.get_tags_query(lang) or ""
        except Exception:
            src = ""
    for pattern in DROP_PATTERNS.get(lang, []):
        src = src.replace(pattern, "")
    return src + APPEND_TAGS.get(lang, "")


@cache
def get_tags_query(lang: str) -> Query | None:
    own = _own_tags_source(lang)
    if not own.strip():
        return None
    parents = [_own_tags_source(p) for p in INHERITS.get(lang, [])]
    query = make_query(lang, "\n".join([*parents, own]))
    if query is None and parents:
        query = make_query(lang, own)
    return query


def run_query(query: Query, node: Node) -> list[tuple[int, dict[str, list[Node]]]]:
    try:
        return QueryCursor(query).matches(node)
    except Exception:
        return []


def node_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'`":
        return s[1:-1]
    return s
