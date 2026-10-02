from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def run_git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=GIT_ENV
    ).stdout


@pytest.fixture
def metis_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("METIS_HOME", str(home))
    return home


@pytest.fixture
def fixture_repo(tmp_path: Path):
    def make(name: str, init_git: bool = True) -> Path:
        dest = tmp_path / name
        shutil.copytree(FIXTURES / name, dest)
        if init_git:
            run_git("init", "-q", "-b", "main", cwd=dest)
            run_git("add", ".", cwd=dest)
            run_git("commit", "-q", "-m", "init", cwd=dest)
        return dest

    return make
