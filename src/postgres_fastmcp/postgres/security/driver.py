"""Исполнитель безопасного SQL: валидация + таймаут + search_path вокруг делегирующего исполнителя."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, cast

from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.errors import QueryTimeoutError


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SafeSqlConfig:
    """Конфигурация SafeSqlExecutor.

    Attributes:
        query_tag: Тег добавляется к запросам для логирования/мониторинга.
        timeout: Необязательный таймаут выполнения в секундах.
        allowed_schema: Разрешенная схема (например, 'public'); None означает все.
        read_only: Если True, только операторы чтения; если False, разрешен DML.
        table_prefix: Если задан вместе с allowed_schema, только таблицы с этим префиксом.
    """

    query_tag: str = "postgres-fastmcp"
    timeout: float | None = None
    allowed_schema: str | None = None
    read_only: bool = True
    table_prefix: str | None = None


class SafeSqlExecutor:
    """Композиционная обертка: валидация SQL, установка search_path/timeout, затем делегирование выполнения."""

    def __init__(
        self,
        delegate: Any,  # noqa: ANN401
        validator: QueryValidator,
        config: SafeSqlConfig,
    ) -> None:
        """Инициализация с делегирующим исполнителем, валидатором и конфигурацией.

        Args:
            delegate: Исполнитель с асинхронным execute(query, params=..., readonly=...) -> list[RowResult]|None.
            validator: Валидатор, используемый для валидации каждого запроса перед выполнением.
            config: Конфигурация безопасного SQL (тег, таймаут, схема, read_only, префикс).
        """
        self._delegate = delegate
        self._validator = validator
        self._config = config

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,  # noqa: ARG002 — part of QueryExecutorPort; effective value from config
    ) -> list[RowResult] | None:
        """Валидация запроса, затем выполнение через делегата (с search_path и необязательным таймаутом).

        Args:
            query: SQL для выполнения.
            params: Необязательные параметры (будут встроены в запрос перед выполнением).
            readonly: Игнорируется; используется self._config.read_only.

        Returns:
            Строки или None для операторов без результата.
        """
        readonly_effective = self._config.read_only
        query = self.render(query, params) if params else f"/* {self._config.query_tag} */ {query}"
        self._validator.validate(query)
        if self._config.allowed_schema:
            query = f"SET LOCAL search_path = {self._config.allowed_schema}; {query}"
        if self._config.timeout is not None:
            try:
                async with asyncio.timeout(self._config.timeout):
                    return cast(
                        "list[RowResult] | None",
                        await self._delegate.execute(query, params=None, readonly=readonly_effective),
                    )
            except TimeoutError as e:
                logger.warning(
                    "Выполнение запроса превысило таймаут %s секунд: %s...",
                    self._config.timeout,
                    query[:100],
                )
                raise QueryTimeoutError(self._config.timeout) from e
        return cast(
            "list[RowResult] | None",
            await self._delegate.execute(query, params=None, readonly=readonly_effective),
        )

    def render(self, query: str, params: list[Any]) -> str:
        """Рендер параметризованного запроса в одну строку (для выполнения без параметров на стороне сервера).

        Args:
            query: Запрос с {} плейсхолдерами (стиль psycopg).
            params: Значения для подстановки.

        Returns:
            Строка запроса с встроенными значениями (с тегом).
        """
        composables = [p if isinstance(p, Composable) else Literal(p) for p in params]
        rendered = SQL(query).format(*composables).as_string()
        return f"/* {self._config.query_tag} */ {rendered}"
