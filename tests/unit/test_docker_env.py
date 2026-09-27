"""Docker: образ слушает все интерфейсы, env.example годится и для --env-file, и для .env."""

import re
from pathlib import Path

import pytest
from dotenv import dotenv_values

from postgres_fastmcp.app.config import Settings


_ROOT = Path(__file__).resolve().parents[2]
_JSON_EXAMPLE = re.compile(r"# (MCP_AUTH_(?:REQUIRED_SCOPES|TOKENS|ACCESS_POLICY__\w+)=.*)")


def test_runtime_image_binds_all_interfaces() -> None:
    runtime = (_ROOT / "Dockerfile").read_text(encoding="utf-8").split("AS production", 1)[1]
    assert "ENV MCP_SERVER_HOST=0.0.0.0" in runtime


def _env_example(tmp_path: Path) -> tuple[str, Path]:
    """env.example с раскомментированными JSON-примерами и static, чтобы tokens проверялись."""
    lines = [
        match.group(1) if (match := _JSON_EXAMPLE.match(line)) else line
        for line in (_ROOT / "env.example").read_text(encoding="utf-8").splitlines()
    ]
    text = "\n".join(lines).replace("MCP_AUTH_MODE=none", "MCP_AUTH_MODE=static") + "\n"
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return text, path


def test_env_example_json_values_load_via_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker --env-file берёт всё после '=' буквально: значения должны совпадать с чтением python-dotenv."""
    text, path = _env_example(tmp_path)
    literal = {
        key: value
        for key, _, value in (
            line.partition("=") for line in text.splitlines() if line.strip() and not line.startswith("#")
        )
    }
    assert literal == dotenv_values(path)
    monkeypatch.chdir(tmp_path.parent)
    for key, value in literal.items():
        monkeypatch.setenv(key, value)
    auth = Settings().auth
    assert (auth.required_scopes, list(auth.tokens)) == (["mcp"], ["<long random string>"])
    assert auth.access_policy.write_values == ["pg:write"]


def test_env_example_loads_as_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, path = _env_example(tmp_path)
    for key in dotenv_values(path):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    auth = Settings().auth
    assert (auth.required_scopes, list(auth.tokens)) == (["mcp"], ["<long random string>"])
    assert auth.access_policy.full_values == ["pg:full"]
