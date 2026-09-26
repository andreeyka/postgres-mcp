"""Тесты описаний тулов в реестре."""

import asyncio
from unittest.mock import MagicMock

from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.tools.definitions import ToolSet
from postgres_fastmcp.tools.registry import register_tools


def _descriptions(*, access_mode: AccessMode, write_mode: bool = False) -> dict[str, str]:
    database = Settings().database.model_copy(update={"access_mode": access_mode, "write_mode": write_mode})
    provider = LocalProvider()
    register_tools(provider, ToolSet(get_db=MagicMock), ceiling=database)
    return {t.name: (t.description or "") for t in asyncio.run(provider.list_tools())}


def test_execute_sql_description_restricted_in_basic_mode() -> None:
    desc = _descriptions(access_mode=AccessMode.BASIC)["execute_sql"]
    assert "read-only" in desc.lower()


def test_execute_sql_description_allows_dml_in_basic_write_mode() -> None:
    """BASIC + write_mode: DML разрешён и коммитится, DDL отклоняется — описание не называет тул read-only."""
    desc = _descriptions(access_mode=AccessMode.BASIC, write_mode=True)["execute_sql"]
    assert "read-only" not in desc.lower()
    assert "INSERT, UPDATE and DELETE" in desc
    assert "DDL is rejected (except CREATE EXTENSION hypopg / pg_stat_statements)" in desc
    assert "public schema" in desc


def test_execute_sql_description_restricted_in_full_without_write_mode() -> None:
    desc = _descriptions(access_mode=AccessMode.FULL, write_mode=False)["execute_sql"]
    assert "read-only" in desc.lower()


def test_execute_sql_description_unrestricted_when_full_and_write_mode() -> None:
    desc = _descriptions(access_mode=AccessMode.FULL, write_mode=True)["execute_sql"]
    lower = desc.lower()
    assert "any sql" in lower or "ddl" in lower


def test_list_objects_description_mentions_public_in_basic() -> None:
    desc = _descriptions(access_mode=AccessMode.BASIC)["list_objects"]
    assert "public" in desc.lower()


def test_list_objects_description_in_full_mentions_schema() -> None:
    desc = _descriptions(access_mode=AccessMode.FULL)["list_objects"]
    assert "specified schema" in desc.lower()


def test_get_object_details_description_mentions_public_in_basic() -> None:
    desc = _descriptions(access_mode=AccessMode.BASIC)["get_object_details"]
    assert "public" in desc.lower()


def test_explain_query_description_present() -> None:
    desc = _descriptions(access_mode=AccessMode.BASIC)["explain_query"]
    assert "execution plan" in desc.lower()


def test_annotation_presets_have_expected_keys() -> None:
    from postgres_fastmcp.tools.registry import DESTRUCTIVE, READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT

    required = {"read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"}
    for preset in (READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT, DESTRUCTIVE):
        assert set(preset.keys()) == required


def test_destructive_preset_marks_writes() -> None:
    from postgres_fastmcp.tools.registry import DESTRUCTIVE

    assert DESTRUCTIVE["read_only_hint"] is False
    assert DESTRUCTIVE["destructive_hint"] is True
    assert DESTRUCTIVE["idempotent_hint"] is False
