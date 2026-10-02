"""Source view: Pygments-highlighted HTML with clickable definitions and call sites.

Highlight colours come from CSS classes on token spans; the "plain" view is the same HTML with
`.plain` on the <pre>, so links work in both modes.
"""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.lexers.special import TextLexer
from pygments.token import STANDARD_TYPES, Name
from pygments.util import ClassNotFound

from metis.model import FileEntry, parse_id
from metis.store import ProjectStore

LEXER = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "go": "go",
    "bash": "bash",
    "sql": "mysql",
    "yaml": "yaml",
    "json": "json",
    "toml": "toml",
    "markdown": "markdown",
    "html": "html",
    "css": "css",
    "scss": "scss",
    "rust": "rust",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "csharp": "csharp",
    "ruby": "ruby",
    "php": "php",
    "kotlin": "kotlin",
    "swift": "swift",
    "lua": "lua",
    "dockerfile": "docker",
    "make": "make",
    "hcl": "terraform",
    "ini": "ini",
    "xml": "xml",
    "scala": "scala",
    "text": "text",
}

# (line, name) -> (node id, css class)
Links = dict[tuple[int, str], tuple[str, str]]


def get_lexer(language: str | None):
    name = LEXER.get(language or "", "text")
    try:
        return get_lexer_by_name(name, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return TextLexer(stripnl=False, ensurenl=False)


def _short_class(ttype) -> str:
    while ttype is not None:
        cls = STANDARD_TYPES.get(ttype)
        if cls:
            return cls
        ttype = ttype.parent
    return ""


def render_source(
    text: str,
    language: str | None,
    links: Links | None = None,
    unresolved: set[tuple[int, str]] | None = None,
    start_line: int = 1,
) -> str:
    links = links or {}
    unresolved = unresolved or set()
    lexer = get_lexer(language)
    line = start_line
    out = ['<pre class="src"><code>', _line_start(line)]
    for ttype, value in lex(text.rstrip("\n"), lexer):
        for i, part in enumerate(value.split("\n")):
            if i > 0:
                line += 1
                out.append("\n" + _line_start(line))
            if not part:
                continue
            html = escape(part)
            key = (line, part)
            if ttype in Name and key in links:
                node, cls = links[key]
                extra = f" {cls}" if cls else ""
                html = f'<a class="src-link{extra}" href="node:{quote(node, safe="")}">{html}</a>'
            elif ttype in Name and key in unresolved:
                html = f'<span class="unres" title="not resolved by the Local Scan">{html}</span>'
            cls = _short_class(ttype)
            out.append(f'<span class="t-{cls}">{html}</span>' if cls else html)
    out.append("</code></pre>")
    return "".join(out)


def _line_start(n: int) -> str:
    return f'<span class="ln" data-line="{n}">{n}</span>'


def source_links(store: ProjectStore, fe: FileEntry) -> tuple[Links, set[tuple[int, str]]]:
    """Definitions link to their own card; resolved call sites link to the edge target."""
    links: Links = {}
    for s in fe.symbols:
        links[(s.start_line, s.name)] = (s.id, "def")
    own = [fe.id, *(s.id for s in fe.symbols)]
    for src in own:
        for e in store.outgoing(src):
            if not e.line or not e.target.startswith("s:"):
                continue
            tsym = store.symbol(e.target)
            name = tsym.name if tsym else parse_id(e.target).qualified_name.rsplit(".", 1)[-1]
            links.setdefault(
                (e.line, name), (e.target, "guess" if e.confidence == "heuristic" else "")
            )
    unresolved = {(r.line, r.name) for r in fe.references if (r.line, r.name) not in links}
    return links, unresolved


def render_node_source(store: ProjectStore, node_id: str) -> tuple[str, int] | None:
    """HTML for a file (whole) or a symbol (its line range). None when unavailable."""
    ref = parse_id(node_id)
    if ref.kind not in ("f", "s") or not ref.repo or ref.path is None:
        return None
    fe = store.file(node_id)
    if fe is None:
        return None
    text = store.read_source(ref.repo, ref.path)
    if text is None:
        return None
    links, unresolved = source_links(store, fe)
    if ref.kind == "s":
        sym = store.symbol(node_id)
        if sym is None:
            return None
        lines = text.splitlines()
        chunk = "\n".join(lines[sym.start_line - 1 : sym.end_line])
        return render_source(
            chunk, fe.language, links, unresolved, sym.start_line
        ), sym.end_line - sym.start_line + 1
    return render_source(text, fe.language, links, unresolved), fe.line_count
