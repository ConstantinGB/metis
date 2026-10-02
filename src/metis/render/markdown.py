"""JSON -> Markdown for one card (node). Pure functions over a ProjectStore.

Links between cards use the `node:<url-encoded id>` scheme; the web layer turns them into
column-browser navigation. The Markdown is also usable on its own.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from urllib.parse import quote

from markdown_it import MarkdownIt

from metis.model import Edge, parse_id
from metis.scan.walker import lang_label
from metis.store import ProjectStore

MAX_LIST = 60

EDGE_VERB = {
    "imports": "imports",
    "calls": "calls",
    "references": "references",
    "inherits": "inherits from",
    "runs": "runs",
    "includes": "includes",
    "handles": "is handled by",
    "defines": "defines",
    "tests": "tests",
}
EDGE_VERB_PASSIVE = {
    "imports": "imported by",
    "calls": "called by",
    "references": "referenced by",
    "inherits": "subclassed by",
    "runs": "run by",
    "includes": "included by",
    "handles": "handles",
    "defines": "defined by",
    "tests": "tested by",
}


@dataclass
class Card:
    node_id: str
    title: str
    subtitle: str
    markdown: str
    ai: bool = False
    source_node: str | None = None
    source_lines: int = 0
    source_open: bool = False
    stage: str | None = None  # ai search cards: "summaries" | "code" | "pending"


def esc(text: str) -> str:
    return "".join("\\" + c if c in "\\`*_{}[]<>#|" else c for c in text)


def code(text: str) -> str:
    """Inline code span; backslash escapes do not work inside backticks, so just drop backticks."""
    return "`" + text.replace("`", "'") + "`"


def loose(store: ProjectStore, repo: str, text: str) -> str:
    """Link when the AI's free-text target can be mapped to a node, plain text otherwise."""
    node = store.resolve_loose(repo, text)
    return link(node, store.label(node, repo)) if node else esc(text)


def link(node_id: str, label: str) -> str:
    return f"[{esc(label)}](node:{quote(node_id, safe='')})"


def fence_lang(lang: str | None) -> str:
    return {"tsx": "tsx", "bash": "bash", "csharp": "csharp"}.get(lang or "", lang or "")


def _edge_list(store: ProjectStore, edges: list[Edge], from_repo: str, passive: bool) -> list[str]:
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for e in sorted(edges, key=lambda e: (e.kind, e.target if not passive else e.source)):
        other = e.source if passive else e.target
        if (other, e.kind) in seen:
            continue
        seen.add((other, e.kind))
        verb = (EDGE_VERB_PASSIVE if passive else EDGE_VERB).get(e.kind, e.kind)
        mark = " *(guess)*" if e.confidence == "heuristic" else ""
        where = f" [line {e.line}](line:{e.line})" if e.line and not passive else ""
        lines.append(f"- {verb} {link(other, store.label(other, from_repo))}{mark}{where}")
        if len(lines) >= MAX_LIST:
            lines.append(f"- ... and {len(edges) - MAX_LIST} more")
            break
    return lines


def _section(title: str, lines: list[str]) -> str:
    if not lines:
        return ""
    return f"\n### {title}\n\n" + "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- cards


def render_card(store: ProjectStore, node_id: str) -> Card:
    ref = parse_id(node_id)
    node_id = ref.id  # normalised form
    if ref.kind == "r":
        return repo_card(store, ref.repo or "")
    if ref.kind == "d":
        return dir_card(store, ref.repo or "", ref.path or "")
    if ref.kind == "f":
        return file_card(store, node_id)
    if ref.kind == "s":
        return symbol_card(store, node_id)
    if ref.kind == "env":
        return env_card(store, node_id)
    return external_card(store, node_id)


def repo_card(store: ProjectStore, repo_name: str) -> Card:
    repo = store.repo(repo_name)
    node_id = f"r:{repo_name}"
    if repo is None:
        return Card(node_id, repo_name, "unknown repo", "This repo is not part of the project.")
    scan = store.scan(repo_name)
    ov = store.ai_overview(repo_name)
    md: list[str] = []
    if ov:
        md.append(f"**{esc(ov.title)}**\n\n{ov.purpose}\n")
        if ov.entry_points:
            md.append(
                _section(
                    "Where execution starts",
                    [f"- {link(e, store.label(e, repo_name))}" for e in ov.entry_points],
                )
            )
        if ov.architecture:
            md.append(f"\n### How it is organised\n\n{ov.architecture}\n")
    if scan is None:
        md.append("\nNo Local Scan yet. Run one from the project page.\n")
        return Card(node_id, repo_name, repo.source.describe(), "\n".join(md), ai=bool(ov))
    st = scan.stats
    langs = ", ".join(
        f"{lang_label(k)} ({v})" for k, v in sorted(st.languages.items(), key=lambda kv: -kv[1])[:8]
    )
    md.append(
        f"\n{st.files} files, {st.symbols} symbols, {st.edges} connections. Languages: {langs or 'none detected'}.\n"
    )
    if not ov:
        entries = [f for f in scan.files if "entry" in f.roles][:12]
        if entries:
            md.append(
                _section(
                    "Where execution starts",
                    [
                        f"- {link(f.id, f.path)}"
                        + (f" — {esc(f.title_hint)}" if f.title_hint else "")
                        for f in entries
                    ],
                )
            )
    routes = [s for f in scan.files for s in f.symbols if s.kind == "route"]
    if routes:
        md.append(_section("HTTP routes", [f"- {link(s.id, s.name)}" for s in routes[:MAX_LIST]]))
    if scan.envs:
        md.append(
            _section(
                "Environment keys", [f"- {link('env:' + e, '$' + e)}" for e in scan.envs[:MAX_LIST]]
            )
        )
    docs = [f for f in scan.files if f.is_doc and f.path.count("/") == 0]
    if docs:
        md.append(_section("Documentation", [f"- {link(f.id, f.path)}" for f in docs[:MAX_LIST]]))
    subdirs, files = store.dir_entries(repo_name, "")
    entries = [f"- {link(f'd:{repo_name}:{d}', d + '/')}" for d in subdirs]
    entries += [f"- {link(f.id, f.path)} · {lang_label(f.language)}" for f in files if not f.is_doc]
    md.append(_section("Top level", entries[:MAX_LIST]))
    if scan.externals:
        md.append(
            _section(
                "External dependencies",
                [f"- {link('x:' + x, x)}" for x in scan.externals[:MAX_LIST]],
            )
        )
    incoming = [e for e in store.incoming(node_id)]
    md.append(_section("Used by other repos", _edge_list(store, incoming, repo_name, passive=True)))
    subtitle = f"{repo.source.describe()} · branch {repo.active_branch}"
    return Card(node_id, repo_name, subtitle, "\n".join(md), ai=bool(ov))


def dir_card(store: ProjectStore, repo_name: str, path: str) -> Card:
    node_id = f"d:{repo_name}:{path}"
    subdirs, files = store.dir_entries(repo_name, path)
    md: list[str] = []
    entries = [f"- {link(f'd:{repo_name}:{posixpath.join(path, d)}', d + '/')}" for d in subdirs]
    for f in files:
        ai = store.ai_summary(repo_name, f.path)
        extra = f" — {esc(ai.title)}" if ai else f" · {lang_label(f.language)}"
        entries.append(f"- {link(f.id, posixpath.basename(f.path))}{extra}")
    md.append(_section("Contents", entries))
    md.append(
        _section("Used by", _edge_list(store, store.incoming(node_id), repo_name, passive=True))
    )
    return Card(node_id, (path or ".") + "/", f"folder in {repo_name}", "\n".join(md))


def file_card(store: ProjectStore, node_id: str) -> Card:
    ref = parse_id(node_id)
    repo_name, path = ref.repo or "", ref.path or ""
    f = store.file(node_id)
    if f is None:
        return Card(
            node_id, posixpath.basename(path), path, "This file is not in the current scan."
        )
    ai = store.ai_summary(repo_name, path)
    title = (
        ai.title
        if ai
        else (f.title_hint or f"{posixpath.basename(path)} - {lang_label(f.language)}")
    )
    md: list[str] = []
    if ai:
        md.append(f"{ai.purpose}\n")
        if ai.stale:
            md.append("\n*This explanation may be outdated: a file it depends on changed since.*\n")
        if ai.notes:
            md.append(f"\n> **Before you edit:** {ai.notes}\n")
    elif f.header_doc:
        md.append(f"{f.header_doc.split(chr(10) + chr(10))[0]}\n")
    else:
        md.append(
            f"{lang_label(f.language)} file, {f.line_count} lines, {len(f.symbols)} symbols. "
            "Run AI Scan for an explanation.\n"
        )
    facts: list[str] = []
    if f.roles:
        facts.append("roles: " + ", ".join(f.roles))
    if f.metrics:
        facts.append(
            f"{f.metrics.get('fan_in', 0)} files use it, it uses {f.metrics.get('fan_out', 0)}"
        )
    hist = store.git_signals(repo_name)
    g = hist.files.get(path) if hist else None
    if g and g.last_changed:
        facts.append(
            f"last changed {g.last_changed.strftime('%Y-%m-%d')}, {g.commits_90d} changes in 90 days"
        )
    if facts:
        md.append("\n" + " · ".join(esc(x) for x in facts) + "\n")
    if g and g.co_changes:
        md.append(
            _section(
                "Usually changed together with",
                [
                    f"- {link(f'f:{repo_name}:{p}', p)} ({n} shared commits)"
                    for p, n in g.co_changes
                ],
            )
        )
    if f.skipped_reason:
        md.append(f"\n*Not parsed: {esc(f.skipped_reason)}.*\n")

    if f.is_doc and f.language == "markdown":
        text = store.read_source(repo_name, path)
        if text:
            md.append("\n---\n\n" + text + "\n")

    ai_fn = {x.symbol_id: x for x in ai.functions} if ai else {}
    top = [s for s in f.symbols if s.parent is None]
    lines: list[str] = []
    for s in top[:MAX_LIST]:
        lines.append(f"- **{s.kind}** {link(s.id, s.qualified_name)} — {code(s.signature)}")
        expl = ai_fn.get(s.id)
        if expl:
            lines.append(f"  {expl.explanation}")
        elif s.docstring:
            lines.append(f"  {esc(s.docstring.splitlines()[0])}")
        for child in [c for c in f.symbols if c.parent == s.id][:20]:
            cx = ai_fn.get(child.id)
            note = f" — {cx.explanation}" if cx else ""
            lines.append(f"  - {child.kind} {link(child.id, child.name)}{note}")
    md.append(_section("Defines", lines))

    imps: list[str] = []
    for imp in f.imports:
        if imp.resolved_to and not imp.is_stdlib:
            imps.append(
                f"- {link(imp.resolved_to.id, store.label(imp.resolved_to.id, repo_name))} · {code(imp.raw)}"
            )
        else:
            imps.append(f"- {code(imp.raw)}" + (" (standard library)" if imp.is_stdlib else ""))
    md.append(_section("Imports and references", imps[:MAX_LIST]))

    if ai and ai.connections:
        rows = []
        for c in ai.connections:
            verb = "uses" if c.direction == "uses" else "is used by"
            rows.append(f"- {verb} {loose(store, repo_name, c.target)} — {c.why}")
        md.append(_section("Connections (AI)", rows))

    rejected = set(ai.rejected_targets) if ai else set()
    own = {node_id} | {s.id for s in f.symbols}
    out = [e for e in store.outgoing(node_id) if e.kind != "imports" and e.target not in rejected]
    out += [e for s in f.symbols for e in store.outgoing(s.id) if e.target not in rejected]
    out = [e for e in out if e.target not in own]
    md.append(_section("Uses", _edge_list(store, out, repo_name, passive=False)))
    inc = list(store.incoming(node_id)) + [e for s in f.symbols for e in store.incoming(s.id)]
    inc = [e for e in inc if e.source not in own]
    md.append(_section("Used by", _edge_list(store, inc, repo_name, passive=True)))

    has_source = not f.skipped_reason and f.line_count > 0
    subtitle = f"{repo_name} · {path}"
    if ai:
        subtitle += f" · AI: {ai.source}" + (" (outdated?)" if ai.stale else "")
    return Card(
        node_id,
        title,
        subtitle,
        "\n".join(md),
        ai=bool(ai),
        source_node=node_id if has_source else None,
        source_lines=f.line_count,
    )


def symbol_card(store: ProjectStore, node_id: str) -> Card:
    ref = parse_id(node_id)
    repo_name, path = ref.repo or "", ref.path or ""
    s = store.symbol(node_id)
    if s is None:
        return Card(
            node_id, ref.qualified_name or node_id, path, "This symbol is not in the current scan."
        )
    f = store.file(node_id)
    ai = store.ai_summary(repo_name, path)
    expl = next((x for x in ai.functions if x.symbol_id == node_id), None) if ai else None
    md: list[str] = []
    if expl:
        md.append(f"{expl.explanation}\n")
        io: list[str] = []
        for t in expl.inputs_from:
            io.append(f"- reads from {loose(store, repo_name, t)}")
        for t in expl.outputs_to:
            io.append(f"- sends to {loose(store, repo_name, t)}")
        md.append(_section("Data flow", io))
    elif s.docstring:
        md.append(f"{esc(s.docstring)}\n")
    if s.parent:
        md.append(f"\nPart of {link(s.parent, store.label(s.parent, repo_name))}.\n")
    children = [c for c in (f.symbols if f else []) if c.parent == node_id]
    md.append(_section("Members", [f"- {c.kind} {link(c.id, c.name)}" for c in children]))
    md.append(
        _section("Uses", _edge_list(store, store.outgoing(node_id), repo_name, passive=False))
    )
    md.append(
        _section("Used by", _edge_list(store, store.incoming(node_id), repo_name, passive=True))
    )
    subtitle = (
        f"{s.kind} in {link(f'f:{repo_name}:{path}', path)} · lines {s.start_line}–{s.end_line}"
    )
    return Card(
        node_id,
        s.qualified_name,
        subtitle,
        "\n".join(md),
        ai=bool(expl),
        source_node=node_id,
        source_lines=s.end_line - s.start_line + 1,
        source_open=True,
    )


def env_card(store: ProjectStore, node_id: str) -> Card:
    name = node_id[4:]
    inc = store.incoming(node_id)
    readers = [e for e in inc if e.kind == "references"]
    setters = [e for e in inc if e.kind == "defines"]
    md = [
        f"Environment or configuration key {code(name)}. Where it is set and who reads it:\n",
        _section("Set by", _edge_list(store, setters, None, passive=True)),
        _section("Read by", _edge_list(store, readers, None, passive=True)),
    ]
    return Card(node_id, f"${name}", "environment key", "\n".join(md))


def external_card(store: ProjectStore, node_id: str) -> Card:
    name = node_id[2:]
    inc = store.incoming(node_id)
    md = [
        f"{code(name)} is a dependency from outside this project, so METIS cannot look inside it.\n",
        _section("Used by", _edge_list(store, inc, None, passive=True)),
    ]
    return Card(node_id, name, "external dependency", "\n".join(md))


# --------------------------------------------------------------------------- search cards

KIND_ORDER = ["file", "class", "function", "method", "table", "view", "key", "constant", "variable"]


def search_card(store: ProjectStore, q: str, limit: int = 80) -> Card:
    q_low = q.strip().lower()
    node_id = f"q:{q.strip()}"
    groups: dict[str, list[str]] = {}
    count = 0
    if q_low:
        for scan in store.all_scans():
            for f in scan.files:
                if q_low in f.path.lower():
                    groups.setdefault("file", []).append(
                        f"- {link(f.id, f'{scan.repo}: {f.path}')}"
                    )
                    count += 1
                for sym in f.symbols:
                    if q_low in sym.name.lower():
                        groups.setdefault(sym.kind, []).append(
                            f"- {link(sym.id, sym.qualified_name)} · {esc(f.path)}"
                        )
                        count += 1
                if count > limit:
                    break
    md: list[str] = []
    if not q_low:
        md.append("Type to search file names, functions, classes, tables and keys.")
    elif not count:
        md.append(f"Nothing matches **{esc(q)}**.")
    for kind in sorted(groups, key=lambda k: (KIND_ORDER.index(k) if k in KIND_ORDER else 99, k)):
        md.append(_section(kind + "s" if kind != "class" else "classes", groups[kind][:40]))
    return Card(
        node_id, f"Search: {q.strip()}", f"{count} match{'es' if count != 1 else ''}", "\n".join(md)
    )


def ai_search_card(store: ProjectStore, q: str, result, stage: str) -> Card:
    """`result` is an AiSearchResult or None (pending)."""
    node_id = f"ai:{q.strip()}"
    if result is None:
        return Card(
            node_id,
            f"AI search: {q.strip()}",
            "asking Claude…",
            "Claude is looking through the project. This card updates itself.",
            stage="pending",
        )
    md: list[str] = []
    if not result.results:
        md.append("Claude found nothing that matches. Try different words, or search deeper.")
    rows = []
    for hit in result.results:
        mark = "" if hit.confidence == "high" else f" *({hit.confidence})*"
        rows.append(f"- {link(hit.node_id, store.label(hit.node_id, None))}{mark} — {esc(hit.why)}")
    md.append(_section("Results", rows))
    if result.stage == "summaries":
        hint = (
            "Claude thinks reading the code would help."
            if result.needs_code
            else "Based on names and AI summaries only."
        )
        md.append(
            f'\n{hint}\n\n<form class="deeper" data-q="{esc(q.strip())}"><button type="submit">Search deeper (reads the code)</button></form>\n'
        )
    else:
        md.append("\nBased on reading the code.\n")
    sub = f"{result.stage} · ${result.cost_usd:.2f}" + (
        f" · {result.model}" if result.model else ""
    )
    return Card(node_id, f"AI search: {q.strip()}", sub, "\n".join(md), stage=result.stage)


def node_title(store: ProjectStore, node_id: str) -> str:
    """Cheap title for tab labels; avoids rendering the whole card."""
    if node_id.startswith("q:") or node_id.startswith("ai:"):
        return node_id.split(":", 1)[1]
    try:
        ref = parse_id(node_id)
    except ValueError:
        return node_id
    if ref.kind == "f" and ref.repo and ref.path:
        ai = store.ai_summary(ref.repo, ref.path)
        return ai.title if ai else posixpath.basename(ref.path)
    if ref.kind == "s":
        return ref.qualified_name or node_id
    return store.label(node_id)


def render_node(store: ProjectStore, node_id: str) -> Card:
    """Any card, including the pseudo-nodes for search results."""
    if node_id.startswith("q:"):
        return search_card(store, node_id[2:])
    if node_id.startswith("ai:"):
        from metis.ai.search import latest_result

        q = node_id[3:]
        result = latest_result(store.name, q)
        return ai_search_card(store, q, result, result.stage if result else "pending")
    return render_card(store, node_id)


# --------------------------------------------------------------------------- html

_md = MarkdownIt("commonmark", {"html": True, "linkify": False}).enable("table")


def to_html(markdown: str) -> str:
    return _md.render(markdown)


def render_inline(markdown: str) -> str:
    return _md.renderInline(markdown)


def render_symbol_page_md(store: ProjectStore, node_id: str) -> str:
    """Whole card as a standalone Markdown document (used for export and tests)."""
    card = render_card(store, node_id)
    return f"# {card.title}\n\n*{card.subtitle}*\n\n{card.markdown}"
