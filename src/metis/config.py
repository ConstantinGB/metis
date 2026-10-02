"""Where METIS keeps its data, and the settings/secrets files."""

from __future__ import annotations

import json
import os
from pathlib import Path

from metis.model import Settings


def metis_home() -> Path:
    home = Path(os.environ.get("METIS_HOME", Path.home() / ".metis"))
    home.mkdir(parents=True, exist_ok=True)
    return home


def projects_dir() -> Path:
    d = metis_home() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return metis_home() / "settings.json"


def secrets_path() -> Path:
    return metis_home() / "secrets.json"


def load_settings() -> Settings:
    p = settings_path()
    if p.exists():
        return Settings.model_validate_json(p.read_text())
    s = Settings()
    save_settings(s)
    return s


def save_settings(settings: Settings) -> None:
    settings_path().write_text(settings.model_dump_json(indent=2))


def _load_secrets() -> dict[str, str]:
    p = secrets_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _save_secrets(data: dict[str, str]) -> None:
    p = secrets_path()
    p.write_text(json.dumps(data, indent=2))
    os.chmod(p, 0o600)


def secret_key(project: str, repo: str) -> str:
    return f"{project}/{repo}"


def get_token(project: str, repo: str) -> str | None:
    return _load_secrets().get(secret_key(project, repo))


def set_token(project: str, repo: str, token: str | None) -> None:
    data = _load_secrets()
    key = secret_key(project, repo)
    if token:
        data[key] = token
    else:
        data.pop(key, None)
    _save_secrets(data)
