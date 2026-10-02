"""Node identifiers shared by every layer.

Formats:
  r:<repo>                  repository
  d:<repo>:<dir>            directory ("" for the repo root)
  f:<repo>:<path>           file
  s:<repo>:<path>#<qname>   symbol inside a file
  x:<name>                  external dependency (package outside the project)
"""

from __future__ import annotations

from dataclasses import dataclass


def repo_id(repo: str) -> str:
    return f"r:{repo}"


def dir_id(repo: str, path: str) -> str:
    return f"d:{repo}:{path.strip('/')}"


def file_id(repo: str, path: str) -> str:
    return f"f:{repo}:{path}"


def symbol_id(repo: str, path: str, qualified_name: str) -> str:
    return f"s:{repo}:{path}#{qualified_name}"


def external_id(name: str) -> str:
    return f"x:{name}"


def env_id(name: str) -> str:
    return f"env:{name}"


@dataclass(frozen=True)
class NodeRef:
    kind: str  # r, d, f, s, x
    repo: str | None
    path: str | None
    qualified_name: str | None
    name: str | None  # only for externals

    @property
    def id(self) -> str:
        if self.kind == "r":
            return repo_id(self.repo or "")
        if self.kind == "d":
            return dir_id(self.repo or "", self.path or "")
        if self.kind == "f":
            return file_id(self.repo or "", self.path or "")
        if self.kind == "s":
            return symbol_id(self.repo or "", self.path or "", self.qualified_name or "")
        if self.kind == "env":
            return env_id(self.name or "")
        return external_id(self.name or "")


def parse_id(node_id: str) -> NodeRef:
    kind, _, rest = node_id.partition(":")
    if kind == "r":
        return NodeRef("r", rest, None, None, None)
    if kind == "x":
        return NodeRef("x", None, None, None, rest)
    if kind == "env":
        return NodeRef("env", None, None, None, rest)
    if kind in ("d", "f"):
        repo, _, path = rest.partition(":")
        if kind == "f" and "#" in path:  # tolerate "f:repo:path#symbol" written by the AI
            path, _, qname = path.partition("#")
            return NodeRef("s", repo, path, qname, None)
        return NodeRef(kind, repo, path, None, None)
    if kind == "s":
        repo, _, remainder = rest.partition(":")
        path, _, qname = remainder.partition("#")
        return NodeRef("s", repo, path, qname, None)
    raise ValueError(f"unknown node id: {node_id!r}")


def repo_of(node_id: str) -> str | None:
    try:
        return parse_id(node_id).repo
    except ValueError:
        return None
