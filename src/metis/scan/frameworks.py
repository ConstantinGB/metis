"""Framework-aware facts: HTTP routes, CLI commands, environment keys read and defined."""

from __future__ import annotations

import posixpath
import re

import yaml

from metis.model import Reference, Symbol, symbol_id

PY_ROUTE = re.compile(
    r"^\s*@(?P<obj>\w+(?:\.\w+)*)\.(?P<method>get|post|put|delete|patch|options|head|route|api_route|websocket)\(\s*"
    r"(?:path\s*=\s*)?[\"'](?P<path>[^\"']*)[\"'](?P<rest>[^\n]*)",
    re.M,
)
PY_METHODS = re.compile(r"methods\s*=\s*[\[(]([^\])]*)[\])]")
PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")
DJANGO_PATH = re.compile(
    r"^\s*(?:re_)?path\(\s*r?[\"'](?P<path>[^\"']*)[\"']\s*,\s*(?P<handler>[\w.]+)", re.M
)
JS_ROUTE = re.compile(
    r"\b(?P<obj>app|router|server|api|r|v1|v2)\.(?P<method>get|post|put|delete|patch|all|use)\(\s*"
    r"[\"'`](?P<path>[^\"'`]+)[\"'`]\s*(?:,\s*(?P<handler>[A-Za-z_$][\w$.]*)\s*[,)])?",
)
TS_DECORATOR = re.compile(
    r"^\s*@(?P<method>Get|Post|Put|Delete|Patch)\(\s*(?:[\"'`](?P<path>[^\"'`]*)[\"'`])?\s*\)\s*\n\s*(?:async\s+)?(?P<handler>\w+)\s*\(",
    re.M,
)
GO_ROUTE = re.compile(
    r"\.(?P<method>GET|POST|PUT|DELETE|PATCH|Get|Post|Put|Delete|Patch|Handle|HandleFunc)\(\s*\"(?P<path>[^\"]+)\"\s*,\s*(?P<handler>[\w.]+)",
)
PHP_ROUTE = re.compile(
    r"Route::(?P<method>get|post|put|delete|patch)\(\s*[\"'](?P<path>[^\"']+)[\"']\s*,\s*\[?\s*(?P<handler>[\w\\:]+)"
)

ENV_READ = {
    "python": re.compile(
        r"os\.environ(?:\.get)?\s*[\[(]\s*[\"'](\w+)[\"']|os\.getenv\(\s*[\"'](\w+)[\"']|\benviron\.get\(\s*[\"'](\w+)[\"']"
    ),
    "javascript": re.compile(r"process\.env\.(\w+)|process\.env\[[\"'](\w+)[\"']\]"),
    "typescript": re.compile(r"process\.env\.(\w+)|process\.env\[[\"'](\w+)[\"']\]"),
    "tsx": re.compile(r"process\.env\.(\w+)|process\.env\[[\"'](\w+)[\"']\]"),
    "go": re.compile(r"os\.(?:Getenv|LookupEnv)\(\s*\"(\w+)\"|viper\.Get\w*\(\s*\"(\w+)\""),
    "ruby": re.compile(r"ENV(?:\.fetch\(|\[)\s*[\"'](\w+)[\"']"),
    "php": re.compile(r"getenv\(\s*[\"'](\w+)[\"']|\$_ENV\[[\"'](\w+)[\"']\]"),
    "java": re.compile(r"System\.getenv\(\s*\"(\w+)\""),
    "kotlin": re.compile(r"System\.getenv\(\s*\"(\w+)\""),
    "csharp": re.compile(r"GetEnvironmentVariable\(\s*\"(\w+)\""),
    "rust": re.compile(r"env::var(?:_os)?\(\s*\"(\w+)\""),
    "bash": re.compile(r"\$\{([A-Z][A-Z0-9_]{2,})(?::?[-=?+][^}]*)?\}"),
}
DOTENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", re.M)
SHELL_EXPORT = re.compile(r"^\s*export\s+([A-Z][A-Z0-9_]*)\s*=", re.M)
ENV_KEYS = {"env", "environment", "env_vars", "envs"}

PY_ADD_PARSER = re.compile(r"add_parser\(\s*[\"']([\w-]+)[\"']")
PY_SET_DEFAULTS = re.compile(r"set_defaults\(\s*\w+\s*=\s*(\w+)")
PY_CLICK = re.compile(
    r"^\s*@(?P<obj>\w+)\.(?:command|group)\(\s*(?:name\s*=\s*)?(?:[\"'](?P<name>[\w-]+)[\"'])?",
    re.M,
)
GO_COBRA_USE = re.compile(r"cobra\.Command\{(?P<body>.*?)\}", re.S)
GO_COBRA_FIELDS = re.compile(r"Use:\s*\"([\w-]+)|Run(?:E)?:\s*(\w+)")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _route_symbol(
    repo: str, path: str, method: str, route: str, line: int, raw: str, seen: set[str]
) -> Symbol:
    name = f"{method.upper()} {route or '/'}"
    qname = name if name not in seen else f"{name}~{line}"
    seen.add(name)
    return Symbol(
        id=symbol_id(repo, path, qname),
        kind="route",
        name=name,
        qualified_name=qname,
        start_line=line,
        end_line=line,
        signature=raw.strip()[:160],
    )


def extract_routes(
    repo: str, path: str, lang: str | None, text: str
) -> tuple[list[Symbol], list[Reference]]:
    symbols: list[Symbol] = []
    refs: list[Reference] = []
    seen: set[str] = set()
    lines = text.splitlines()

    def handler_after(line: int, limit: int = 6) -> str | None:
        for i in range(line, min(line + limit, len(lines))):
            m = PY_DEF.match(lines[i])
            if m:
                return m.group(1)
        return None

    if lang == "python":
        for m in PY_ROUTE.finditer(text):
            line = _line_of(text, m.start())
            methods = [m.group("method")]
            if methods[0] in ("route", "api_route"):
                mm = PY_METHODS.search(m.group("rest"))
                methods = (
                    [x.strip(" \"'").lower() for x in mm.group(1).split(",") if x.strip()]
                    if mm
                    else ["any"]
                )
            handler = handler_after(line)
            for method in methods:
                symbols.append(
                    _route_symbol(repo, path, method, m.group("path"), line, m.group(0), seen)
                )
            if handler:
                refs.append(Reference(name=handler, line=line, kind="handler"))
        for m in DJANGO_PATH.finditer(text):
            line = _line_of(text, m.start())
            symbols.append(
                _route_symbol(repo, path, "any", m.group("path"), line, m.group(0), seen)
            )
            refs.append(
                Reference(name=m.group("handler").rsplit(".", 1)[-1], line=line, kind="handler")
            )
    elif lang in ("javascript", "typescript", "tsx"):
        for m in JS_ROUTE.finditer(text):
            line = _line_of(text, m.start())
            symbols.append(
                _route_symbol(
                    repo, path, m.group("method"), m.group("path"), line, m.group(0), seen
                )
            )
            if m.group("handler"):
                refs.append(
                    Reference(name=m.group("handler").rsplit(".", 1)[-1], line=line, kind="handler")
                )
        for m in TS_DECORATOR.finditer(text):
            line = _line_of(text, m.start())
            symbols.append(
                _route_symbol(
                    repo,
                    path,
                    m.group("method"),
                    m.group("path") or "",
                    line,
                    m.group(0).split("\n")[0],
                    seen,
                )
            )
            refs.append(Reference(name=m.group("handler"), line=line, kind="handler"))
    elif lang == "go":
        for m in GO_ROUTE.finditer(text):
            line = _line_of(text, m.start())
            method = m.group("method")
            method = "any" if method in ("Handle", "HandleFunc") else method
            symbols.append(
                _route_symbol(repo, path, method, m.group("path"), line, m.group(0), seen)
            )
            refs.append(
                Reference(name=m.group("handler").rsplit(".", 1)[-1], line=line, kind="handler")
            )
    elif lang == "php":
        for m in PHP_ROUTE.finditer(text):
            line = _line_of(text, m.start())
            symbols.append(
                _route_symbol(
                    repo, path, m.group("method"), m.group("path"), line, m.group(0), seen
                )
            )
            refs.append(
                Reference(
                    name=m.group("handler").split("::")[-1].split("\\")[-1],
                    line=line,
                    kind="handler",
                )
            )
    return symbols, refs


def extract_commands(
    repo: str, path: str, lang: str | None, text: str
) -> tuple[list[Symbol], list[Reference]]:
    symbols: list[Symbol] = []
    refs: list[Reference] = []
    seen: set[str] = set()

    def cmd(name: str, line: int, raw: str) -> None:
        qname = f"command:{name}" if f"command:{name}" not in seen else f"command:{name}~{line}"
        seen.add(f"command:{name}")
        symbols.append(
            Symbol(
                id=symbol_id(repo, path, qname),
                kind="command",
                name=name,
                qualified_name=qname,
                start_line=line,
                end_line=line,
                signature=raw.strip()[:160],
            )
        )

    if lang == "python":
        lines = text.splitlines()
        for m in PY_ADD_PARSER.finditer(text):
            line = _line_of(text, m.start())
            cmd(m.group(1), line, lines[line - 1])
            window = "\n".join(lines[line - 1 : line + 14])
            mm = PY_SET_DEFAULTS.search(window)
            if mm:
                refs.append(Reference(name=mm.group(1), line=line, kind="handler"))
        for m in PY_CLICK.finditer(text):
            line = _line_of(text, m.start())
            handler = None
            for i in range(line, min(line + 6, len(lines))):
                d = PY_DEF.match(lines[i])
                if d:
                    handler = d.group(1)
                    break
            name = m.group("name") or (handler.replace("_", "-") if handler else None)
            if name:
                cmd(name, line, lines[line - 1])
                if handler:
                    refs.append(Reference(name=handler, line=line, kind="handler"))
    elif lang == "go":
        for m in GO_COBRA_USE.finditer(text):
            line = _line_of(text, m.start())
            use = run = None
            for mm in GO_COBRA_FIELDS.finditer(m.group("body")):
                use = use or mm.group(1)
                run = run or mm.group(2)
            if use:
                cmd(use.split()[0], line, text.splitlines()[line - 1])
                if run:
                    refs.append(Reference(name=run, line=line, kind="handler"))
    return symbols, refs


def extract_env_reads(lang: str | None, text: str) -> list[Reference]:
    pat = ENV_READ.get(lang or "")
    if pat is None:
        return []
    out: list[Reference] = []
    seen: set[tuple[str, int]] = set()
    for m in pat.finditer(text):
        name = next((g for g in m.groups() if g), None)
        if not name:
            continue
        line = _line_of(text, m.start())
        if (name, line) in seen:
            continue
        seen.add((name, line))
        out.append(Reference(name=name, line=line, kind="env"))
    return out


def extract_env_defines(path: str, lang: str | None, text: str) -> list[Reference]:
    name = posixpath.basename(path)
    out: list[Reference] = []
    seen: set[str] = set()

    def add(key: str, line: int) -> None:
        if key and key not in seen and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            seen.add(key)
            out.append(Reference(name=key, line=line, kind="env_define"))

    if name.startswith(".env") or name.endswith(".env"):
        for m in DOTENV_LINE.finditer(text):
            add(m.group(1), _line_of(text, m.start()))
    elif lang == "bash":
        for m in SHELL_EXPORT.finditer(text):
            add(m.group(1), _line_of(text, m.start()))
    elif lang == "yaml":
        try:
            docs = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
        except yaml.YAMLError:
            return out

        def walk(node: yaml.Node) -> None:
            if isinstance(node, yaml.MappingNode):
                for k, v in node.value:
                    if isinstance(k, yaml.ScalarNode) and k.value in ENV_KEYS:
                        if isinstance(v, yaml.MappingNode):
                            for kk, _vv in v.value:
                                if isinstance(kk, yaml.ScalarNode):
                                    add(str(kk.value), kk.start_mark.line + 1)
                        elif isinstance(v, yaml.SequenceNode):
                            for item in v.value:
                                if isinstance(item, yaml.ScalarNode):
                                    add(
                                        str(item.value).split("=", 1)[0].strip(),
                                        item.start_mark.line + 1,
                                    )
                                elif isinstance(item, yaml.MappingNode):  # k8s style: - name: X
                                    for kk, vv in item.value:
                                        if (
                                            isinstance(kk, yaml.ScalarNode)
                                            and kk.value == "name"
                                            and isinstance(vv, yaml.ScalarNode)
                                        ):
                                            add(str(vv.value), vv.start_mark.line + 1)
                    walk(v)
            elif isinstance(node, yaml.SequenceNode):
                for item in node.value:
                    walk(item)

        for d in docs:
            if d is not None:
                walk(d)
    return out
