"""Тесты описаний тулов в реестре."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.tools.registry import register_tools


def _make_settings(*, access_mode: AccessMode, write_mode: bool = False) -> Settings:
    s = Settings()
    s.database = s.database.model_copy(
        update={"access_mode": access_mode, "write_mode": write_mode}
    )
    return s


def _descriptions(mcp: FastMCP) -> dict[str, str]:
    result = asyncio.run(mcp.list_tools())
    if isinstance(result, dict):
        return {name: (t.description or "") for name, t in result.items()}
    return {t.name: (t.description or "") for t in result}


def test_execute_sql_description_restricted_in_basic_mode() -> None:
    mcp = FastMCP(name="t")
    register_tools(mcp, _make_settings(access_mode=AccessMode.BASIC))
    desc = _descriptions(mcp)["execute_sql"]
    assert "read-only" in desc.lower()


def test_execute_sql_description_restricted_in_full_without_write_mode() -> None:
    mcp = FastMCP(name="t")
    register_tools(mcp, _make_settings(access_mode=AccessMode.FULL, write_mode=False))
    desc = _descriptions(mcp)["execute_sql"]
    assert "read-only" in desc.lower()


def test_execute_sql_description_unrestricted_when_full_and_write_mode() -> None:
    mcp = FastMCP(name="t")
    register_tools(mcp, _make_settings(access_mode=AccessMode.FULL, write_mode=True))
    desc = _descriptions(mcp)["execute_sql"]
    lower = desc.lower()
    assert "any sql" in lower or "ddl" in lower


def test_list_objects_description_mentions_public_in_basic() -> None:
    mcp = FastMCP(name="t")
    register_tools(mcp, _make_settings(access_mode=AccessMode.BASIC))
    desc = _descriptions(mcp)["list_objects"]
    assert "public" in desc.lower()


def test_list_objects_description_in_full_mentions_schema() -> None:
    mcp = FastMCP(name="t")
    register_tools(mcp, _make_settings(access_mode=AccessMode.FULL))
    desc = _descriptions(mcp)["list_objects"]
    assert "specified schema" in desc.lower()


def test_get_object_details_description_mentions_public_in_basic() -> None:
    mcp = FastMCP(name="t")
    register_tools(mcp, _make_settings(access_mode=AccessMode.BASIC))
    desc = _descriptions(mcp)["get_object_details"]
    assert "public" in desc.lower()


def test_explain_query_description_present() -> None:
    mcp = FastMCP(name="t")
    register_tools(mcp, _make_settings(access_mode=AccessMode.BASIC))
    desc = _descriptions(mcp)["explain_query"]
    assert "execution plan" in desc.lower()


def test_annotation_presets_have_expected_keys() -> None:
    from postgres_fastmcp.tools.registry import DESTRUCTIVE, READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT

    required = {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
    for preset in (READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT, DESTRUCTIVE):
        assert set(preset.keys()) == required


def test_destructive_preset_marks_writes() -> None:
    from postgres_fastmcp.tools.registry import DESTRUCTIVE

    assert DESTRUCTIVE["readOnlyHint"] is False
    assert DESTRUCTIVE["destructiveHint"] is True
    assert DESTRUCTIVE["idempotentHint"] is False
