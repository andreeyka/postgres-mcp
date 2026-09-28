"""Исполнитель служебных запросов каталога: только шаблоны сервера и строковые параметры, только чтение."""

from typing import Any

from postgres_fastmcp.postgres.catalog import CATALOG_QUERIES
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.ports import SqlDriverPort
from postgres_fastmcp.postgres.security.driver import SafeSqlConfig, SafeSqlExecutor
from postgres_fastmcp.postgres.security.query_validator import QueryValidator


class CatalogSqlExecutor:
    """Выполняет только запросы из CATALOG_QUERIES: read-only транзакция, statement_timeout, AST-валидация.

    Граница доверия: SQL агента сюда не попадает — execute_sql идёт через sql_driver. Схему и
    префикс имени проверяет домен каталога до запроса, поэтому валидатор здесь без схемы и
    префикса (правила full read-only). Параметры — только str: SafeSqlExecutor.render()
    вставляет Composable как SQL-фрагмент, а строка всегда становится Literal.

    Реализует только execute (QueryExecutorPort): как SqlDriverPort его не передать.
    """

    def __init__(self, delegate: SqlDriverPort, *, timeout: float | None, query_tag: str) -> None:
        """Инициализация поверх исполнителя без проверок.

        Args:
            delegate: Исполнитель без проверок (SqlExecutor) на пуле сервиса.
            timeout: statement_timeout в секундах; None — без таймаута.
            query_tag: Тег запросов для логирования/мониторинга.
        """
        config = SafeSqlConfig(query_tag=query_tag, timeout=timeout, read_only=True)
        self._inner = SafeSqlExecutor(delegate=delegate, validator=QueryValidator(read_only=True), config=config)

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,  # noqa: ARG002 — part of QueryExecutorPort; always read-only
    ) -> list[RowResult] | None:
        """Выполнить шаблон каталога со строковыми параметрами.

        Args:
            query: Шаблон из CATALOG_QUERIES (с {} плейсхолдерами).
            params: Строковые параметры шаблона.
            readonly: Игнорируется: исполнитель всегда только читает.

        Returns:
            Строки результата или None.

        Raises:
            ValueError: query не входит в CATALOG_QUERIES.
            TypeError: Параметр не str (в том числе Composable).
        """
        if query not in CATALOG_QUERIES:
            msg = "Only server catalog queries can run on the catalog executor"
            raise ValueError(msg)
        for param in params or []:
            if type(param) is not str:
                msg = f"Catalog query parameters must be str, got {type(param).__name__}"
                raise TypeError(msg)
        return await self._inner.execute(query, params)
