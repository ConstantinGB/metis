"""Optional precision tier: ask an installed language server for go-to-definition on call sites the
tree-sitter pass could only guess at. Never required; failures leave the scan untouched."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from metis.model import Edge, FileEntry, LocalScan, Symbol, parse_id
from metis.scan.symbols import enclosing_symbol

SERVERS: dict[str, list[str]] = {
    "python": ["pyright-langserver", "--stdio"],
    "go": ["gopls"],
    "typescript": ["typescript-language-server", "--stdio"],
    "tsx": ["typescript-language-server", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
}
LANGUAGE_IDS = {
    "python": "python",
    "go": "go",
    "typescript": "typescript",
    "tsx": "typescriptreact",
    "javascript": "javascript",
}
REQUEST_TIMEOUT = 15.0
LANGUAGE_BUDGET_S = 120.0
MAX_LOOKUPS_PER_FILE = 150


def available() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for lang, cmd in SERVERS.items():
        if shutil.which(cmd[0]):
            out[lang] = cmd
    return out


class LspClient:
    """Minimal JSON-RPC over stdio. Only what refine() needs."""

    def __init__(self, cmd: list[str], root: Path):
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=root
        )
        self.root = root
        self._id = 0
        self.initialize()

    # -- transport ------------------------------------------------------------

    def _send(self, msg: dict) -> None:
        data = json.dumps(msg).encode()
        assert self.proc.stdin
        self.proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        self.proc.stdin.flush()

    def _read(self) -> dict | None:
        assert self.proc.stdout
        length = 0
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1].strip())
        body = self.proc.stdout.read(length)
        return json.loads(body) if body else None

    def request(
        self, method: str, params: dict, timeout: float = REQUEST_TIMEOUT
    ) -> dict | list | None:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self._read()
            if msg is None:
                return None
            if msg.get("id") == rid:
                return msg.get("result")
            # notifications and server->client requests are ignored (reply to requests with null)
            if "id" in msg and "method" in msg:
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
        return None

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # -- protocol -------------------------------------------------------------

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": self.root.as_uri(),
                "capabilities": {"textDocument": {"definition": {"linkSupport": False}}},
                "workspaceFolders": [{"uri": self.root.as_uri(), "name": self.root.name}],
            },
            timeout=30,
        )
        self.notify("initialized", {})

    def open(self, rel: str, language_id: str, text: str) -> None:
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": (self.root / rel).as_uri(),
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )

    def close(self, rel: str) -> None:
        self.notify("textDocument/didClose", {"textDocument": {"uri": (self.root / rel).as_uri()}})

    def definition(self, rel: str, line: int, col: int) -> list[dict]:
        res = self.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": (self.root / rel).as_uri()},
                "position": {"line": line, "character": col},
            },
        )
        if res is None:
            return []
        return res if isinstance(res, list) else [res]

    def shutdown(self) -> None:
        try:
            self.request("shutdown", {}, timeout=5)
            self.notify("exit", {})
        finally:
            try:
                self.proc.kill()
            except OSError:
                pass


def location_to_symbol(root: Path, scan: LocalScan, loc: dict) -> Symbol | None:
    uri = loc.get("uri") or loc.get("targetUri")
    rng = loc.get("range") or loc.get("targetSelectionRange") or loc.get("targetRange")
    if not uri or not rng:
        return None
    path = Path(unquote(urlparse(uri).path))
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    fe = scan.file_by_path().get(rel)
    if fe is None:
        return None
    line = rng["start"]["line"] + 1
    exact = [s for s in fe.symbols if s.start_line == line and s.kind not in ("route", "command")]
    if exact:
        return exact[0]
    return enclosing_symbol([s for s in fe.symbols if s.kind not in ("route", "command")], line)


def _targets_needing_help(scan: LocalScan) -> dict[str, list[tuple[FileEntry, int, int, str]]]:
    """Per language: (file, line, col, source id) for call sites without an exact edge."""
    exact_at: set[tuple[str, int]] = set()
    guessed_at: set[tuple[str, int]] = set()
    for e in scan.edges:
        if e.kind not in ("calls", "inherits") or not e.line:
            continue
        sp = parse_id(e.source)
        (exact_at if e.confidence == "exact" else guessed_at).add((sp.path or "", e.line))
    out: dict[str, list[tuple[FileEntry, int, int, str]]] = {}
    for f in scan.files:
        if f.language not in SERVERS or f.skipped_reason:
            continue
        n = 0
        for r in f.references:
            if (
                r.kind != "call"
                or (f.path, r.line) in exact_at
                and (f.path, r.line) not in guessed_at
            ):
                continue
            src = enclosing_symbol(
                [s for s in f.symbols if s.kind not in ("route", "command")], r.line
            )
            out.setdefault(f.language, []).append((f, r.line, r.col, src.id if src else f.id))
            n += 1
            if n >= MAX_LOOKUPS_PER_FILE:
                break
    return out


def refine(
    root: Path,
    scan: LocalScan,
    cache_dir: Path,
    progress=None,
    servers: dict[str, list[str]] | None = None,
) -> dict[str, int]:
    """Upgrade or add call edges using installed language servers. Returns per-language counts."""
    servers = available() if servers is None else servers
    work = _targets_needing_help(scan)
    if not servers or not work:
        return {}
    cache_path = cache_dir / "lsp-cache.json"
    try:
        cache: dict = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    except (OSError, ValueError):
        cache = {}
    edge_index = {(e.source, e.target, e.kind): e for e in scan.edges}
    counts: dict[str, int] = {}
    for lang, items in work.items():
        cmd = servers.get(lang)
        if not cmd:
            continue
        try:
            client = LspClient(cmd, root)
        except (OSError, AssertionError):
            continue
        started = time.time()
        done = 0
        try:
            by_file: dict[str, list[tuple[FileEntry, int, int, str]]] = {}
            for item in items:
                by_file.setdefault(item[0].path, []).append(item)
            for path, lookups in by_file.items():
                if time.time() - started > LANGUAGE_BUDGET_S:
                    break
                fe = lookups[0][0]
                file_cache: dict = cache.setdefault(fe.content_hash, {})
                text = None
                opened = False
                for _fe, line, col, source in lookups:
                    key = f"{line}:{col}"
                    if key in file_cache:
                        target = file_cache[key]
                    else:
                        if not opened:
                            try:
                                text = (root / path).read_text(errors="replace")
                            except OSError:
                                break
                            client.open(path, LANGUAGE_IDS[lang], text)
                            opened = True
                        locs = client.definition(path, line - 1, col)
                        sym = next(
                            (s for s in (location_to_symbol(root, scan, loc) for loc in locs) if s),
                            None,
                        )
                        target = sym.id if sym else None
                        file_cache[key] = target
                    if target and target != source:
                        key_e = (source, target, "calls")
                        if key_e in edge_index:
                            e = edge_index[key_e]
                            if e.confidence != "exact":
                                e.confidence = "exact"
                                e.note = "lsp"
                                done += 1
                        else:
                            e = Edge(
                                source=source,
                                target=target,
                                kind="calls",
                                confidence="exact",
                                line=line,
                                note="lsp",
                            )
                            edge_index[key_e] = e
                            scan.edges.append(e)
                            done += 1
                if opened:
                    client.close(path)
                if progress:
                    progress(0, 0, f"language server ({lang}): {path}")
        finally:
            client.shutdown()
        counts[lang] = done
    scan.edges.sort(key=Edge.key)
    scan.stats.lsp = counts
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    except OSError:
        pass
    return counts
