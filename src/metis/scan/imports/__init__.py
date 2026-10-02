from __future__ import annotations

from metis.scan.imports.base import Extraction, LanguageHandler, RepoInfo, ScanContext
from metis.scan.imports.bash import BashHandler
from metis.scan.imports.go import GoHandler
from metis.scan.imports.javascript import JavaScriptHandler
from metis.scan.imports.python import PythonHandler
from metis.scan.imports.sql import SqlHandler
from metis.scan.imports.yaml_ import YamlHandler

_HANDLERS: dict[str, LanguageHandler] = {}
for _h in (
    PythonHandler(),
    JavaScriptHandler(),
    GoHandler(),
    YamlHandler(),
    BashHandler(),
    SqlHandler(),
):
    for _lang in _h.languages:
        _HANDLERS[_lang] = _h


def get_handler(lang: str | None) -> LanguageHandler | None:
    return _HANDLERS.get(lang or "")


__all__ = ["Extraction", "LanguageHandler", "RepoInfo", "ScanContext", "get_handler"]
