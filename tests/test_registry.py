from __future__ import annotations

from conftest import run_git

from metis import config
from metis.projects import registry
from metis.scan.runner import load_repo_scan, scan_repo


def test_project_lifecycle(metis_home, fixture_repo):
    root = fixture_repo("pyproj")
    run_git("checkout", "-q", "-b", "feature", cwd=root)
    (root / "alpha" / "extra.py").write_text("def extra():\n    return 1\n")
    run_git("add", ".", cwd=root)
    run_git("commit", "-q", "-m", "feature", cwd=root)
    run_git("checkout", "-q", "main", cwd=root)

    project = registry.create_project("Demo Project")
    assert project.name == "Demo-Project"
    repo = registry.add_repo(project, str(root))
    assert repo.is_git and repo.default_branch == "main"
    assert set(repo.branches) == {"main", "feature"}
    assert "alpha" in repo.package_names

    scan = scan_repo(project, repo, "main")
    assert scan.stats.files > 5
    assert load_repo_scan(project, repo, "main") is not None

    path = registry.checkout_path(project, repo, "feature")
    assert (path / "alpha" / "extra.py").exists()
    assert path != root
    feature = scan_repo(project, repo, "feature")
    assert "alpha/extra.py" in feature.file_by_path()
    assert "alpha/extra.py" not in scan.file_by_path()

    reloaded = registry.load_project(project.name)
    assert reloaded.repo("pyproj").checkouts["feature"] == str(path)
    assert [p.name for p in registry.list_projects()] == ["Demo-Project"]

    registry.delete_project(project.name)
    assert registry.list_projects() == []
    assert not path.exists()


def test_settings_and_secrets(metis_home):
    s = config.load_settings()
    assert s.model == "claude-sonnet-5" and s.budget_usd_per_run == 5.0 and s.theme == "dark"
    s.model = "claude-opus-5"
    config.save_settings(s)
    assert config.load_settings().model == "claude-opus-5"
    config.set_token("p", "r", "tok")
    assert config.get_token("p", "r") == "tok"
    assert oct(config.secrets_path().stat().st_mode)[-3:] == "600"
    config.set_token("p", "r", None)
    assert config.get_token("p", "r") is None


def test_repo_without_commits(metis_home, fixture_repo):
    root = fixture_repo("pyproj", init_git=False)
    run_git("init", "-q", "-b", "main", cwd=root)  # no commit yet: HEAD is unborn
    project = registry.create_project("fresh")
    repo = registry.add_repo(project, str(root))
    assert repo.is_git and repo.default_branch == "main" and repo.branches == ["main"]
    scan = scan_repo(project, repo)
    assert scan.commit is None
    assert "alpha/core.py" in scan.file_by_path()
