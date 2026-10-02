"""Thin subprocess wrapper around the git CLI. Tokens are passed per command, never stored."""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def _auth_args(token: str | None) -> list[str]:
    if not token:
        return []
    b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraheader=AUTHORIZATION: basic {b64}"]


def run_git(
    args: list[str],
    cwd: Path | None = None,
    token: str | None = None,
    check: bool = True,
) -> str:
    cmd = ["git", *_auth_args(token), *args]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        # never echo the command: it may contain the auth header
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def clone(url: str, dest: Path, token: str | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_git(["clone", "--quiet", url, str(dest)], token=token)


def fetch(path: Path, token: str | None = None) -> None:
    run_git(["fetch", "--quiet", "--all", "--prune"], cwd=path, token=token, check=False)


def current_branch(path: Path) -> str:
    # symbolic-ref works even before the first commit (an "unborn" branch); rev-parse does not.
    out = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=path, check=False).strip()
    if out:
        return out
    out = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path, check=False).strip()
    return out or "HEAD"


def default_branch(path: Path) -> str:
    out = run_git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=path, check=False)
    out = out.strip()
    if out.startswith("origin/"):
        return out[len("origin/") :]
    return current_branch(path)


def list_branches(path: Path) -> list[str]:
    out = run_git(["branch", "-a", "--format=%(refname:short)"], cwd=path, check=False)
    names: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or line.endswith("/HEAD") or line == "HEAD":
            continue
        if line.startswith("origin/"):
            line = line[len("origin/") :]
        names.add(line)
    return sorted(names)


def head_commit(path: Path, ref: str = "HEAD") -> str | None:
    out = run_git(
        ["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], cwd=path, check=False
    ).strip()
    return out or None


def add_worktree(repo_path: Path, dest: Path, branch: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    local = run_git(["branch", "--list", branch], cwd=repo_path, check=False).strip()
    if local:
        run_git(["worktree", "add", str(dest), branch], cwd=repo_path)
    else:
        run_git(
            ["worktree", "add", "--track", "-b", branch, str(dest), f"origin/{branch}"],
            cwd=repo_path,
        )


def remove_worktree(repo_path: Path, dest: Path) -> None:
    run_git(["worktree", "remove", "--force", str(dest)], cwd=repo_path, check=False)


def prune_worktrees(repo_path: Path) -> None:
    run_git(["worktree", "prune"], cwd=repo_path, check=False)


def ls_files(path: Path) -> list[str]:
    """Tracked plus untracked-but-not-ignored files, relative to the repo root."""
    out = run_git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=path)
    return sorted({p for p in out.split("\0") if p})


def diff_numstat(path: Path, base: str, head: str) -> list[tuple[int, int, str]]:
    out = run_git(["diff", "--numstat", f"{base}...{head}"], cwd=path, check=False)
    rows: list[tuple[int, int, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, f = parts
        rows.append((int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0, f))
    return rows


def diff_file(path: Path, base: str, head: str, file: str) -> str:
    return run_git(["diff", f"{base}...{head}", "--", file], cwd=path, check=False)


def diff_full(path: Path, base: str, head: str) -> str:
    return run_git(["diff", f"{base}...{head}"], cwd=path, check=False)


def log_between(path: Path, base: str, head: str) -> list[str]:
    out = run_git(["log", "--oneline", f"{base}..{head}"], cwd=path, check=False)
    return [line for line in out.splitlines() if line.strip()]
