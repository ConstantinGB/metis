"""Directory walk, ignore rules, language detection, safe file reading."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from metis.projects import git

DEFAULT_IGNORES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    ".vscode",
    "target",
    ".next",
    ".nuxt",
    ".cache",
    "coverage",
    ".DS_Store",
    ".eggs",
    "site-packages",
    ".terraform",
    ".gradle",
    "bin",
    "obj",
}

LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".lua": "lua",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".mysql": "sql",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".xml": "xml",
    ".ini": "ini",
    ".cfg": "ini",
    ".md": "markdown",
    ".rst": "markdown",
    ".txt": "text",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".tf": "hcl",
    ".hcl": "hcl",
    ".dockerfile": "dockerfile",
}

LANGUAGE_BY_NAME: dict[str, str] = {
    "dockerfile": "dockerfile",
    "makefile": "make",
    "cmakelists.txt": "cmake",
    "jenkinsfile": "groovy",
    "rakefile": "ruby",
    "gemfile": "ruby",
}

# Languages whose symbols/imports METIS understands (parsed with tree-sitter or custom code)
LANGUAGE_LABEL = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "tsx": "TSX",
    "go": "Go",
    "rust": "Rust",
    "java": "Java",
    "kotlin": "Kotlin",
    "c": "C",
    "cpp": "C++",
    "csharp": "C#",
    "ruby": "Ruby",
    "php": "PHP",
    "bash": "Shell",
    "sql": "SQL",
    "yaml": "YAML",
    "json": "JSON",
    "toml": "TOML",
    "markdown": "Markdown",
    "html": "HTML",
    "css": "CSS",
    "dockerfile": "Dockerfile",
    "make": "Makefile",
    "hcl": "Terraform",
    "swift": "Swift",
    "lua": "Lua",
    "scala": "Scala",
    "text": "Text",
    "ini": "INI",
    "xml": "XML",
    "scss": "SCSS",
}


def lang_label(lang: str | None) -> str:
    return LANGUAGE_LABEL.get(lang or "", (lang or "file").capitalize())


DOC_EXTS = {".md", ".rst", ".txt"}
DOC_NAMES = {"readme", "changelog", "contributing", "license", "authors", "claude", "agents"}


def list_files(root: Path) -> list[str]:
    """Repo-relative posix paths. Uses git when available so .gitignore is respected."""
    if git.is_git_repo(root):
        try:
            files = git.ls_files(root)
            return [f for f in files if (root / f).is_file()]
        except git.GitError:
            pass
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in DEFAULT_IGNORES)
        rel_dir = os.path.relpath(dirpath, root)
        for fn in sorted(filenames):
            if fn in DEFAULT_IGNORES:
                continue
            rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            out.append(rel.replace(os.sep, "/"))
    return out


def detect_language(rel_path: str) -> str | None:
    name = rel_path.rsplit("/", 1)[-1]
    lowered = name.lower()
    if lowered in LANGUAGE_BY_NAME:
        return LANGUAGE_BY_NAME[lowered]
    if lowered.startswith("dockerfile"):
        return "dockerfile"
    _, dot, ext = name.rpartition(".")
    if not dot:
        return None
    return LANGUAGE_BY_EXT.get("." + ext.lower())


def is_doc(rel_path: str) -> bool:
    name = rel_path.rsplit("/", 1)[-1].lower()
    stem, _, ext = name.rpartition(".")
    if "." + ext in DOC_EXTS:
        return True
    return stem in DOC_NAMES or name in DOC_NAMES or rel_path.lower().startswith("docs/")


def read_file(root: Path, rel_path: str, max_bytes: int) -> tuple[bytes | None, str | None]:
    """Return (data, None) or (None, reason) when the file should not be parsed."""
    p = root / rel_path
    try:
        size = p.stat().st_size
    except OSError as e:
        return None, f"unreadable: {e.strerror}"
    if size > max_bytes:
        return None, f"too large ({size} bytes)"
    try:
        data = p.read_bytes()
    except OSError as e:
        return None, f"unreadable: {e.strerror}"
    if b"\0" in data[:8000]:
        return None, "binary"
    return data, None


def content_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


def count_lines(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)
