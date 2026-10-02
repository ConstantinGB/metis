"""The left column: every repo as a collapsible folder tree of node links."""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from metis.model import FileEntry
from metis.store import ProjectStore


def _href(node_id: str) -> str:
    return f"node:{quote(node_id, safe='')}"


class _Dir:
    __slots__ = ("dirs", "files")

    def __init__(self) -> None:
        self.dirs: dict[str, _Dir] = {}
        self.files: list[FileEntry] = []


def _build(files: list[FileEntry]) -> _Dir:
    root = _Dir()
    for f in files:
        node = root
        parts = f.path.split("/")
        for part in parts[:-1]:
            node = node.dirs.setdefault(part, _Dir())
        node.files.append(f)
    return root


def _render_dir(repo: str, path: str, node: _Dir, done: set[str], depth: int) -> str:
    out: list[str] = []
    for name in sorted(node.dirs):
        sub = node.dirs[name]
        sub_path = f"{path}/{name}" if path else name
        open_attr = (
            " open" if depth == 0 and not name.startswith(".") and len(node.dirs) <= 3 else ""
        )
        out.append(
            f'<details class="dir"{open_attr}><summary><a href="{_href(f"d:{repo}:{sub_path}")}">'
            f"{escape(name)}/</a></summary>{_render_dir(repo, sub_path, sub, done, depth + 1)}</details>"
        )
    for f in sorted(node.files, key=lambda x: x.path):
        name = f.path.rsplit("/", 1)[-1]
        cls = "file ai" if f.path in done else "file"
        title = f' title="{escape(f.language)}"' if f.language else ""
        out.append(f'<a class="{cls}" href="{_href(f.id)}"{title}>{escape(name)}</a>')
    return "".join(out)


def render_tree(store: ProjectStore) -> str:
    parts: list[str] = []
    for repo in store.project.repos:
        scan = store.scan(repo.name)
        parts.append(
            f'<details class="repo" open><summary><a href="{_href(f"r:{repo.name}")}">'
            f'{escape(repo.name)}</a> <span class="muted">{escape(repo.active_branch)}</span></summary>'
        )
        if scan is None:
            parts.append('<p class="muted">no scan yet</p>')
        else:
            done = set(store.ai_index(repo.name).hashes)
            parts.append(_render_dir(repo.name, "", _build(scan.files), done, 0))
        parts.append("</details>")
    if not parts:
        return '<p class="muted">Add a repo to the project first.</p>'
    return "".join(parts)
