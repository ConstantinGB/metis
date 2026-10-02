"""Projects, repos, checkouts. Everything under ~/.metis/projects/<name>/."""

from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path

from metis import config
from metis.model import Project, Repo, RepoSource, now
from metis.projects import git

LOCAL_BRANCH = "local"  # pseudo-branch for folders that are not git repositories


class RegistryError(RuntimeError):
    pass


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-._")
    if not slug:
        raise RegistryError(f"cannot derive a name from {name!r}")
    return slug


def branch_slug(branch: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-") or "branch"


# --------------------------------------------------------------------------- projects


def project_dir(name: str) -> Path:
    return config.projects_dir() / name


def project_file(name: str) -> Path:
    return project_dir(name) / "project.json"


def list_projects() -> list[Project]:
    out: list[Project] = []
    for d in sorted(config.projects_dir().iterdir()):
        f = d / "project.json"
        if f.exists():
            out.append(Project.model_validate_json(f.read_text()))
    return out


def load_project(name: str) -> Project:
    f = project_file(name)
    if not f.exists():
        raise RegistryError(f"project {name!r} does not exist")
    return Project.model_validate_json(f.read_text())


def save_project(project: Project) -> None:
    project.updated_at = now()
    d = project_dir(project.name)
    d.mkdir(parents=True, exist_ok=True)
    project_file(project.name).write_text(project.model_dump_json(indent=2))


def create_project(name: str) -> Project:
    slug = slugify(name)
    if project_file(slug).exists():
        raise RegistryError(f"project {slug!r} already exists")
    project = Project(name=slug)
    save_project(project)
    return project


def delete_project(name: str) -> None:
    project = load_project(name)
    for repo in project.repos:
        if repo.source.kind == "local" and repo.is_git:
            root = Path(repo.source.path or "")
            for branch, path in repo.checkouts.items():
                if branch != repo.default_branch:
                    git.remove_worktree(root, Path(path))
            git.prune_worktrees(root)
        config.set_token(project.name, repo.name, None)
    shutil.rmtree(project_dir(name), ignore_errors=True)


# --------------------------------------------------------------------------- repos


def looks_like_url(source: str) -> bool:
    s = source.strip()
    return s.startswith(("http://", "https://", "git@", "ssh://")) or s.startswith("github.com/")


def repo_name_from_source(source: str) -> str:
    s = source.strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    return slugify(s.rsplit("/", 1)[-1].rsplit(":", 1)[-1])


def detect_packages(root: Path) -> tuple[list[str], str | None]:
    """Names other repos might import this repo by, plus the Go module path."""
    names: set[str] = set()
    go_module: str | None = None
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text())
            name = data.get("project", {}).get("name") or data.get("tool", {}).get(
                "poetry", {}
            ).get("name")
            if name:
                names.add(name)
                names.add(name.replace("-", "_"))
        except (tomllib.TOMLDecodeError, OSError):
            pass
    setup = root / "setup.py"
    if setup.exists():
        m = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", setup.read_text(errors="ignore"))
        if m:
            names.add(m.group(1))
            names.add(m.group(1).replace("-", "_"))
    for base in (root, root / "src"):
        if base.is_dir():
            for child in base.iterdir():
                if child.is_dir() and (child / "__init__.py").exists():
                    names.add(child.name)
    pkg = root / "package.json"
    if pkg.exists():
        m = re.search(r'"name"\s*:\s*"([^"]+)"', pkg.read_text(errors="ignore"))
        if m:
            names.add(m.group(1))
    gomod = root / "go.mod"
    if gomod.exists():
        m = re.search(r"^module\s+(\S+)", gomod.read_text(errors="ignore"), re.M)
        if m:
            go_module = m.group(1)
            names.add(go_module)
    return sorted(names), go_module


def add_repo(project: Project, source: str, token: str | None = None) -> Repo:
    source = source.strip()
    name = repo_name_from_source(source)
    if any(r.name == name for r in project.repos):
        raise RegistryError(f"repo {name!r} already in project {project.name!r}")

    if looks_like_url(source):
        url = source if not source.startswith("github.com/") else f"https://{source}"
        dest = project_dir(project.name) / "repos" / name
        if dest.exists():
            shutil.rmtree(dest)
        git.clone(url, dest, token=token)
        repo = Repo(name=name, source=RepoSource(kind="github", url=url), is_git=True)
        root = dest
        if token:
            config.set_token(project.name, name, token)
            repo.has_token = True
    else:
        root = Path(source).expanduser().resolve()
        if not root.is_dir():
            raise RegistryError(f"local path does not exist: {root}")
        repo = Repo(
            name=name,
            source=RepoSource(kind="local", path=str(root)),
            is_git=git.is_git_repo(root),
        )

    if repo.is_git:
        repo.default_branch = git.default_branch(root)
        repo.branches = git.list_branches(root) or [repo.default_branch]
        if repo.default_branch not in repo.branches:
            repo.branches.insert(0, repo.default_branch)
    else:
        repo.default_branch = LOCAL_BRANCH
        repo.branches = [LOCAL_BRANCH]
    repo.active_branch = repo.default_branch
    repo.checkouts[repo.default_branch] = str(root)
    repo.package_names, repo.go_module = detect_packages(root)

    project.repos.append(repo)
    save_project(project)
    return repo


def remove_repo(project: Project, repo_name: str) -> None:
    repo = project.repo(repo_name)
    root = Path(repo.checkouts.get(repo.default_branch, ""))
    if repo.is_git and root.exists():
        for branch, path in repo.checkouts.items():
            if branch != repo.default_branch:
                git.remove_worktree(root, Path(path))
        git.prune_worktrees(root)
    if repo.source.kind == "github":
        shutil.rmtree(project_dir(project.name) / "repos" / repo.name, ignore_errors=True)
    shutil.rmtree(project_dir(project.name) / "worktrees" / repo.name, ignore_errors=True)
    shutil.rmtree(project_dir(project.name) / "scans" / repo.name, ignore_errors=True)
    config.set_token(project.name, repo.name, None)
    project.repos = [r for r in project.repos if r.name != repo_name]
    save_project(project)


def refresh_branches(project: Project, repo: Repo) -> None:
    if not repo.is_git:
        return
    root = Path(repo.checkouts[repo.default_branch])
    git.fetch(root, token=config.get_token(project.name, repo.name))
    repo.branches = git.list_branches(root) or [repo.default_branch]
    if repo.default_branch not in repo.branches:
        repo.branches.insert(0, repo.default_branch)
    save_project(project)


def checkout_path(project: Project, repo: Repo, branch: str) -> Path:
    """Path of a checkout for `branch`, creating a worktree on first use."""
    if branch in repo.checkouts:
        p = Path(repo.checkouts[branch])
        if p.exists():
            return p
    if not repo.is_git:
        raise RegistryError(f"{repo.name} is not a git repository; only {LOCAL_BRANCH} exists")
    if branch not in repo.branches:
        refresh_branches(project, repo)
        if branch not in repo.branches:
            raise RegistryError(f"branch {branch!r} not found in {repo.name}")
    main = Path(repo.checkouts[repo.default_branch])
    dest = project_dir(project.name) / "worktrees" / repo.name / branch_slug(branch)
    if dest.exists():
        git.remove_worktree(main, dest)
        shutil.rmtree(dest, ignore_errors=True)
    git.add_worktree(main, dest, branch)
    repo.checkouts[branch] = str(dest)
    save_project(project)
    return dest


def set_active_branch(project: Project, repo: Repo, branch: str) -> Path:
    path = checkout_path(project, repo, branch)
    repo.active_branch = branch
    save_project(project)
    return path


def scans_dir(project_name: str, repo_name: str, branch: str) -> Path:
    d = project_dir(project_name) / "scans" / repo_name / branch_slug(branch)
    d.mkdir(parents=True, exist_ok=True)
    return d
