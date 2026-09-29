"""Упаковка: имя консольного скрипта и версия пакета (§5 спеки)."""

import tomllib
from pathlib import Path


_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_console_script_is_postgres_fastmcp() -> None:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["scripts"] == {"postgres-fastmcp": "postgres_fastmcp.app.main:app"}


def test_version_is_0_2_1() -> None:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["version"] == "0.2.1"
