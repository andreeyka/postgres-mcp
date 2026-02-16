"""Исполнитель безопасного SQL: валидация + таймаут + search_path вокруг делегирующего исполнителя."""

import asyncio
import logging
from typing import Any, cast

from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.sql.models.row_result import RowResult
from postgres_fastmcp.sql.security.config import SafeSqlConfig
from postgres_fastmcp.sql.validation.query_validator import QueryValidator


logger = logging.getLogger(__name__)


class SafeSqlExecutor:
    """Композиционная обертка: валидация SQL, установка search_path/timeout, затем делегирование выполнения."""

    def __init__(
        self,
        delegate: Any,  # QueryExecutorPort-compatible: execute(query, params?, readonly) -> list[RowResult]|None
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
        readonly: bool = True,
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
                raise ValueError(
                    f"Выполнение запроса превысило таймаут {self._config.timeout} секунд в режиме ограничения. "
                    "Рассмотрите возможность упрощения запроса или увеличения таймаута."
                ) from e
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
