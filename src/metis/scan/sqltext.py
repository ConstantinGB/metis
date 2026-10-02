"""Find SQL statements inside string literals of code and extract the tables they touch."""

from __future__ import annotations

import re

from metis.model import Reference

STRING = re.compile(
    r'"""(?P<a>.*?)"""|\'\'\'(?P<b>.*?)\'\'\'|`(?P<c>[^`]*)`|"(?P<d>(?:[^"\\\n]|\\.)*)"|\'(?P<e>(?:[^\'\\\n]|\\.)*)\'',
    re.S,
)
SQL_START = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE|WITH|CREATE|ALTER|DROP|TRUNCATE|MERGE)\b", re.I
)
TABLE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TRUNCATE(?:\s+TABLE)?|DELETE\s+FROM|TABLE(?:\s+IF\s+(?:NOT\s+)?EXISTS)?)\s+"
    r"[`\"\[]?(?P<name>[A-Za-z_][\w.]*)[`\"\]]?",
    re.I,
)
NOT_TABLES = {"select", "dual", "values", "set", "where", "on", "duplicate"}


def table_refs(text: str) -> list[Reference]:
    """`Reference(kind="table")` for each (table, line) mentioned in SQL-looking string literals."""
    out: list[Reference] = []
    seen: set[tuple[str, int]] = set()
    for m in STRING.finditer(text):
        body = next((g for g in m.groups() if g is not None), "")
        if not body or not SQL_START.match(body):
            continue
        base_line = text.count("\n", 0, m.start()) + 1
        for t in TABLE.finditer(body):
            name = t.group("name").split(".")[-1]
            if name.lower() in NOT_TABLES or name.startswith("{"):
                continue
            line = base_line + body.count("\n", 0, t.start())
            if (name, line) in seen:
                continue
            seen.add((name, line))
            out.append(Reference(name=name, line=line, kind="table"))
    return out
