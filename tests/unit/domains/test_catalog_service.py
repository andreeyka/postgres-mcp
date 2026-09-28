# mypy: ignore-errors
"""Unit tests for CatalogService (facade: list_schemas, list_objects, get_object_details)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import postgres_fastmcp.domains.db_access as db_access_module
from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.catalog.service import CatalogService
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.postgres.catalog import (
    QUERY_GET_COLUMNS,
    QUERY_GET_CONSTRAINTS,
    QUERY_GET_INDEXES,
    QUERY_TABLE_EXISTS,
)
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import (
    ObjectNotFoundError,
    SchemaAccessError,
    SystemRelationAccessError,
    TablePrefixAccessError,
    UnsupportedObjectTypeError,
)


_PRESENT = [RowResult(cells={"present": 1})]


def _catalog_rows(*, exists: bool):
    """Подмена execute для деталей таблицы: запрос существования отвечает exists, остальные — пусто."""

    async def execute(query, params=None, *, readonly=True):
        if query is QUERY_TABLE_EXISTS:
            return _PRESENT if exists else []
        return []

    return execute


class TestCatalogServiceListObjects:
    """Tests for CatalogService.list_objects."""

    async def test_list_objects_table_returns_list_with_schema_name_type(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """object_type=table returns list of dicts with schema, name, type."""
        mock_executor.execute.return_value = [
            RowResult(cells={"table_schema": "public", "table_name": "users", "table_type": "BASE TABLE"}),
        ]
        service = CatalogService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="table")
        assert len(result) == 1
        assert result[0]["schema"] == "public"
        assert result[0]["name"] == "users"
        assert result[0]["type"] == "BASE TABLE"

    async def test_list_objects_view_returns_list_with_type(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """object_type=view returns list with type VIEW."""
        mock_executor.execute.return_value = [
            RowResult(cells={"table_schema": "public", "table_name": "v1", "table_type": "VIEW"}),
        ]
        service = CatalogService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="view")
        assert len(result) == 1
        assert result[0]["type"] == "VIEW"

    async def test_list_objects_sequence_returns_list_with_name_and_data_type(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """object_type=sequence returns list with name and data_type."""
        mock_executor.execute.return_value = [
            RowResult(cells={"sequence_schema": "public", "sequence_name": "seq1", "data_type": "bigint"}),
        ]
        service = CatalogService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="sequence")
        assert len(result) == 1
        assert result[0]["name"] == "seq1"
        assert result[0]["data_type"] == "bigint"

    async def test_list_objects_sequence_basic_filters_by_table_prefix(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """BASIC: sequences are filtered by the table_prefix of the request's DbAccess."""
        mock_db_access.access_mode = AccessMode.BASIC
        mock_db_access.table_prefix = "app_"
        mock_executor.execute.return_value = [
            RowResult(cells={"sequence_schema": "public", "sequence_name": "app_seq", "data_type": "bigint"}),
            RowResult(cells={"sequence_schema": "public", "sequence_name": "other_seq", "data_type": "bigint"}),
        ]
        service = CatalogService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="sequence")
        assert [row["name"] for row in result] == ["app_seq"]

    async def test_list_objects_extension_returns_list_with_name(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """object_type=extension returns list with extension name."""
        mock_executor.execute.return_value = [
            RowResult(cells={"extname": "plpgsql", "extversion": "1.0", "extrelocatable": False}),
        ]
        service = CatalogService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="extension")
        assert len(result) == 1
        assert result[0]["name"] == "plpgsql"

    async def test_list_objects_unsupported_type_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """Unsupported object_type raises UnsupportedObjectTypeError with that type."""
        service = CatalogService(db=mock_db_access)
        with pytest.raises(UnsupportedObjectTypeError) as exc_info:
            await service.list_objects(schema_name="public", object_type="trigger")
        assert exc_info.value.object_type == "trigger"

    async def test_list_objects_basic_access_mode_non_public_schema_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """access_mode=basic requesting non-public schema raises SchemaAccessError."""
        mock_db_access.access_mode = AccessMode.BASIC
        service = CatalogService(db=mock_db_access)
        with pytest.raises(SchemaAccessError) as exc_info:
            await service.list_objects(schema_name="other", object_type="table")
        assert exc_info.value.schema_name == "other"

    async def test_list_objects_basic_access_mode_public_schema_returns_catalog_result(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """access_mode=basic with schema_name public returns catalog result (e.g. empty list when no tables)."""
        mock_db_access.access_mode = AccessMode.BASIC
        mock_executor.execute.return_value = []
        service = CatalogService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="table")
        assert result == []


class TestCatalogServiceGetObjectDetails:
    """Tests for CatalogService.get_object_details."""

    async def test_get_object_details_table_returns_basic_columns_constraints_indexes(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """get_object_details for table returns dict with basic, columns, constraints, indexes."""
        mock_executor.execute.side_effect = [
            [
                RowResult(cells={"column_name": "id", "data_type": "int", "is_nullable": "NO", "column_default": None}),
            ],
            [],
            [],
            _PRESENT,
        ]
        service = CatalogService(db=mock_db_access)
        result = await service.get_object_details("public", "users", "table")
        assert result["basic"]["schema"] == "public"
        assert result["basic"]["name"] == "users"
        assert result["basic"]["type"] == "table"
        assert len(result["columns"]) == 1
        assert result["columns"][0]["column"] == "id"
        assert result["constraints"] == []
        assert result["indexes"] == []

    async def test_get_object_details_table_runs_four_queries_in_parallel(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """columns/constraints/indexes and the existence query run concurrently."""
        import asyncio
        import time

        async def slow_execute(query, *args, **kwargs):
            await asyncio.sleep(0.15)
            return _PRESENT if query is QUERY_TABLE_EXISTS else []

        mock_executor.execute.side_effect = slow_execute
        service = CatalogService(db=mock_db_access)
        start = time.perf_counter()
        await service.get_object_details("public", "users", "table")
        elapsed = time.perf_counter() - start
        assert elapsed < 0.3, f"Expected parallel execution (<0.3s), got {elapsed:.3f}s"

    async def test_empty_table_exists(self, mock_db_access: MagicMock, mock_executor: MagicMock) -> None:
        """CREATE TABLE t(): каталог знает таблицу, разделы пустые — это не «не найдено»."""
        mock_executor.execute.side_effect = _catalog_rows(exists=True)
        service = CatalogService(db=mock_db_access)

        result = await service.get_object_details("public", "t_empty", "table")

        assert result == {
            "basic": {"schema": "public", "name": "t_empty", "type": "table"},
            "columns": [],
            "constraints": [],
            "indexes": [],
        }

    @pytest.mark.parametrize(
        ("object_type", "table_type", "other_type"),
        [("table", "BASE TABLE", "view"), ("view", "VIEW", "table")],
    )
    async def test_missing_table_or_view_raises_not_found(
        self, mock_db_access: MagicMock, mock_executor: MagicMock, object_type: str, table_type: str, other_type: str
    ) -> None:
        """Нет строки в information_schema.tables с нужным table_type — ObjectNotFoundError."""
        mock_executor.execute.side_effect = _catalog_rows(exists=False)
        service = CatalogService(db=mock_db_access)

        with pytest.raises(ObjectNotFoundError) as exc_info:
            await service.get_object_details("public", "ghost", object_type)

        assert str(exc_info.value) == (
            f"Object not found: public.ghost ({object_type}). "
            f'If it is a {other_type}, retry with object_type="{other_type}"; use list_objects to see existing objects.'
        )
        exists_call = next(c for c in mock_executor.execute.await_args_list if c.args[0] is QUERY_TABLE_EXISTS)
        assert exists_call.kwargs["params"] == ["public", "ghost", table_type]

    async def test_missing_sequence_raises_not_found(self, mock_db_access: MagicMock, mock_executor: MagicMock) -> None:
        mock_executor.execute.return_value = []
        service = CatalogService(db=mock_db_access)

        with pytest.raises(ObjectNotFoundError, match=r"Object not found: public\.ghost \(sequence\)"):
            await service.get_object_details("public", "ghost", "sequence")

    async def test_missing_extension_message_has_no_schema(
        self, mock_db_access: MagicMock, mock_executor: MagicMock
    ) -> None:
        """Расширения не принадлежат схеме: в сообщении только имя."""
        mock_executor.execute.return_value = []
        service = CatalogService(db=mock_db_access)

        with pytest.raises(ObjectNotFoundError) as exc_info:
            await service.get_object_details("public", "ghost", "extension")

        assert str(exc_info.value) == "Object not found: ghost (extension). Use list_objects to see existing objects."

    async def test_failed_query_cancels_sibling_queries(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """Один запрос упал сразу (как валидатор): соседи отменены, а наружу идёт исходное исключение, не группа."""
        import asyncio

        started: set[str] = set()
        cancelled: set[str] = set()
        names = {
            QUERY_GET_COLUMNS: "columns",
            QUERY_GET_CONSTRAINTS: "constraints",
            QUERY_TABLE_EXISTS: "exists",
        }

        async def execute(query, *args, **kwargs):
            if query is QUERY_GET_INDEXES:
                raise TablePrefixAccessError("pg_indexes", "app_")
            name = names[query]
            started.add(name)
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.add(name)
                raise
            return []

        mock_executor.execute.side_effect = execute
        service = CatalogService(db=mock_db_access)

        with pytest.raises(TablePrefixAccessError, match="pg_indexes"):
            await asyncio.wait_for(service.get_object_details("public", "users", "table"), timeout=2)

        assert started == {"columns", "constraints", "exists"}
        assert cancelled == started
        # Ошибка запроса не оставляет вызывающей задаче «висящий» запрос отмены.
        assert asyncio.current_task().cancelling() == 0

    async def test_request_cancellation_wins_over_a_failed_query(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """Запрос отменили в ту же итерацию, когда упал дочерний запрос: наружу CancelledError, а не ошибка домена."""
        import asyncio

        service = CatalogService(db=mock_db_access)
        outer: dict[str, asyncio.Task] = {}

        async def execute(query, *args, **kwargs):
            if query is QUERY_GET_INDEXES:
                # Отмена вызывающей задачи и падение ребёнка — в одной итерации цикла.
                outer["task"].cancel()
                raise TablePrefixAccessError("pg_indexes", "app_")
            await asyncio.sleep(10)
            return []

        mock_executor.execute.side_effect = execute
        outer["task"] = asyncio.create_task(service.get_object_details("public", "users", "table"))

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(outer["task"], timeout=2)
        assert outer["task"].cancelled()

    async def test_get_object_details_unsupported_type_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """Unsupported object_type raises UnsupportedObjectTypeError."""
        service = CatalogService(db=mock_db_access)
        with pytest.raises(UnsupportedObjectTypeError) as exc_info:
            await service.get_object_details("public", "x", "trigger")
        assert exc_info.value.object_type == "trigger"

    async def test_get_object_details_basic_access_mode_non_public_schema_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """access_mode=basic requesting non-public schema raises SchemaAccessError."""
        mock_db_access.access_mode = AccessMode.BASIC
        service = CatalogService(db=mock_db_access)
        with pytest.raises(SchemaAccessError):
            await service.get_object_details("other", "t", "table")


class TestCatalogServiceListSchemas:
    """Tests for CatalogService.list_schemas."""

    async def test_list_schemas_basic_access_mode_returns_public_only(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """access_mode=basic returns hardcoded public schema without calling DB."""
        mock_db_access.access_mode = AccessMode.BASIC
        service = CatalogService(db=mock_db_access)
        result = await service.list_schemas()
        assert result == [
            {
                "schema_name": "public",
                "schema_owner": "postgres",
                "schema_type": "User Schema",
            },
        ]
        mock_db_access.sql_driver.execute.assert_not_called()

    async def test_list_schemas_full_access_mode_returns_decoded_catalog_rows(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """access_mode=full returns list of dicts from the catalog; driver called once with readonly."""
        mock_executor.execute.return_value = [
            RowResult(cells={"schema_name": "public", "schema_owner": "postgres", "schema_type": "User Schema"}),
            RowResult(cells={"schema_name": "ext", "schema_owner": "postgres", "schema_type": "User Schema"}),
        ]
        service = CatalogService(db=mock_db_access)
        result = await service.list_schemas()
        assert len(result) == 2
        assert result[0]["schema_name"] == "public"
        assert result[0]["schema_owner"] == "postgres"
        assert result[1]["schema_name"] == "ext"
        mock_executor.execute.assert_called_once()
        call_kw = mock_executor.execute.call_args[1]
        assert call_kw.get("readonly") is True

    async def test_list_schemas_full_access_mode_empty_result(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """access_mode=full when catalog returns no rows returns empty list."""
        mock_executor.execute.return_value = []
        service = CatalogService(db=mock_db_access)
        result = await service.list_schemas()
        assert result == []
        mock_executor.execute.assert_called_once()


_PREFIX_ERROR = "Access to table '{}' is not allowed. Only tables with names starting with 'app_' are permitted."


def _fake_postgres(*, present: bool):
    """Ответы делегата по тексту отрендеренного запроса: объект есть (present) или нет."""

    async def execute(query, params=None, *, readonly=True):
        if "SELECT 1 AS present" in query:
            return [RowResult(cells={"present": 1})] if present else []
        if "pg_catalog.pg_indexes" in query:
            row = {"indexname": "app_users_name_idx", "indexdef": "CREATE INDEX app_users_name_idx ON public.app_users"}
            return [RowResult(cells=row)] if present else []
        if "information_schema.sequences" in query:
            row = {
                "sequence_schema": "public",
                "sequence_name": "app_users_id_seq",
                "data_type": "integer",
                "start_value": "1",
                "increment": "1",
            }
            return [RowResult(cells=row)] if present else []
        if "pg_catalog.pg_extension" in query:
            return [RowResult(cells={"extname": "plpgsql", "extversion": "1.0", "extrelocatable": False})]
        return []

    return execute


@pytest.fixture
def fake_delegate(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Делегат вместо SqlExecutor: настоящие SafeSqlExecutor и CatalogSqlExecutor собирает DbAccessService."""
    delegate = MagicMock()
    delegate.execute = AsyncMock(side_effect=_fake_postgres(present=True))
    monkeypatch.setattr(db_access_module, "SqlExecutor", lambda conn: delegate)
    return delegate


def _basic_prefix_db(*, write_mode: bool) -> DbAccess:
    config = DatabaseConfig(
        host="h",
        user="u",
        password="p",
        name="d",
        access_mode=AccessMode.BASIC,
        write_mode=write_mode,
        table_prefix="app_",
    )
    return DbAccessService(config).view(EffectiveAccess(AccessMode.BASIC, write_mode=write_mode))


@pytest.mark.parametrize("write_mode", [False, True])
class TestBasicTablePrefix:
    """BASIC с table_prefix="app_" на настоящей цепочке исполнителей поверх фальшивого делегата."""

    @pytest.mark.parametrize("object_type", ["table", "view"])
    async def test_prefixed_relation_details_include_indexes(
        self, fake_delegate: MagicMock, object_type: str, *, write_mode: bool
    ) -> None:
        result = await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
            "public", "app_users", object_type
        )

        assert result["basic"] == {"schema": "public", "name": "app_users", "type": object_type}
        assert result["indexes"] == [
            {"name": "app_users_name_idx", "definition": "CREATE INDEX app_users_name_idx ON public.app_users"}
        ]
        sent = [c.args[0] for c in fake_delegate.execute.await_args_list]
        assert any("pg_catalog.pg_indexes" in q for q in sent)
        # Каталог только читает, даже при write_mode=True
        assert all(c.kwargs["readonly"] is True for c in fake_delegate.execute.await_args_list)

    @pytest.mark.usefixtures("fake_delegate")
    async def test_prefix_match_ignores_case(self, *, write_mode: bool) -> None:
        result = await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
            "public", "APP_Users", "table"
        )
        assert result["basic"]["name"] == "APP_Users"

    @pytest.mark.parametrize("object_type", ["table", "view", "sequence"])
    async def test_unprefixed_object_is_rejected_before_any_query(
        self, fake_delegate: MagicMock, object_type: str, *, write_mode: bool
    ) -> None:
        """Ответ одинаков для существующего и несуществующего объекта: каталог не опрашивается."""
        with pytest.raises(TablePrefixAccessError) as exc_info:
            await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
                "public", "users", object_type
            )

        assert str(exc_info.value) == _PREFIX_ERROR.format("users")
        fake_delegate.execute.assert_not_awaited()

    @pytest.mark.parametrize("object_type", ["table", "view", "sequence"])
    async def test_missing_prefixed_object_is_not_found(
        self, fake_delegate: MagicMock, object_type: str, *, write_mode: bool
    ) -> None:
        fake_delegate.execute.side_effect = _fake_postgres(present=False)

        with pytest.raises(ObjectNotFoundError):
            await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
                "public", "app_ghost", object_type
            )

    async def test_object_name_reaches_postgres_as_a_literal(
        self, fake_delegate: MagicMock, *, write_mode: bool
    ) -> None:
        fake_delegate.execute.side_effect = _fake_postgres(present=False)

        with pytest.raises(ObjectNotFoundError):
            await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
                "public", "app_x' OR 1=1 --", "table"
            )

        sent = [c.args[0] for c in fake_delegate.execute.await_args_list]
        assert len(sent) == 4
        assert all("'app_x'' OR 1=1 --'" in q for q in sent)

    @pytest.mark.usefixtures("fake_delegate")
    async def test_prefixed_sequence_details(self, *, write_mode: bool) -> None:
        result = await CatalogService(_basic_prefix_db(write_mode=write_mode)).get_object_details(
            "public", "app_users_id_seq", "sequence"
        )
        assert result["name"] == "app_users_id_seq"

    @pytest.mark.usefixtures("fake_delegate")
    async def test_extensions_ignore_the_prefix(self, *, write_mode: bool) -> None:
        """Имя расширения не принадлежит схеме: префикс к нему не применяется."""
        service = CatalogService(_basic_prefix_db(write_mode=write_mode))

        details = await service.get_object_details("public", "plpgsql", "extension")
        listed = await service.list_objects("public", "extension")

        assert details["name"] == "plpgsql"
        assert [e["name"] for e in listed] == ["plpgsql"]

    @pytest.mark.parametrize("object_type", ["table", "view", "sequence", "extension"])
    async def test_other_schema_is_rejected_before_any_query(
        self, fake_delegate: MagicMock, object_type: str, *, write_mode: bool
    ) -> None:
        service = CatalogService(_basic_prefix_db(write_mode=write_mode))

        with pytest.raises(SchemaAccessError):
            await service.get_object_details("other", "app_users", object_type)
        with pytest.raises(SchemaAccessError):
            await service.list_objects("other", object_type)

        fake_delegate.execute.assert_not_awaited()

    async def test_list_schemas_does_not_query(self, fake_delegate: MagicMock, *, write_mode: bool) -> None:
        schemas = await CatalogService(_basic_prefix_db(write_mode=write_mode)).list_schemas()

        assert [s["schema_name"] for s in schemas] == ["public"]
        fake_delegate.execute.assert_not_awaited()

    @pytest.mark.parametrize(
        ("sql", "error"),
        [
            ("SELECT * FROM pg_indexes", SystemRelationAccessError),
            ("SELECT * FROM pg_catalog.pg_indexes", SystemRelationAccessError),
            ("SELECT * FROM pg_catalog.pg_class", SystemRelationAccessError),
            ("SELECT * FROM other_users", TablePrefixAccessError),
        ],
    )
    async def test_agent_sql_driver_is_still_restricted(
        self, fake_delegate: MagicMock, sql: str, error: type[Exception], *, write_mode: bool
    ) -> None:
        with pytest.raises(error):
            await _basic_prefix_db(write_mode=write_mode).sql_driver.execute(sql)

        fake_delegate.execute.assert_not_awaited()
