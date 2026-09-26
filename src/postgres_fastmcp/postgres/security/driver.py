"""Исполнитель безопасного SQL: валидация + statement_timeout + search_path вокруг делегирующего исполнителя."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, cast

from psycopg.errors import QueryCanceled
from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.errors import QueryCancelledError, QueryTimeoutError


logger = logging.getLogger(__name__)

MS_PER_SECOND = 1000

# Фрагмент diag.message_primary, по которому Postgres сообщает об отмене по statement_timeout
# ("canceling statement due to statement timeout"). Сервер должен отдавать сообщения на английском (lc_messages).
_STATEMENT_TIMEOUT_MARKER = "statement timeout"


@dataclass(frozen=True, slots=True)
class SafeSqlConfig:
    """Конфигурация SafeSqlExecutor.

    Attributes:
        query_tag: Тег добавляется к запросам для логирования/мониторинга.
        timeout: Таймаут выполнения в секундах; выставляется как statement_timeout в Postgres.
        allowed_schema: Разрешенная схема (например, 'public'); None означает все.
        read_only: Если True, только операторы чтения; если False, разрешен DML.
        table_prefix: Если задан вместе с allowed_schema, только таблицы с этим префиксом.
        client_timeout_grace: Запас в секундах для клиентской страховки поверх statement_timeout.
            Обычно срабатывает Postgres; клиентский таймаут ловит зависшее соединение.
    """

    query_tag: str = "postgres-fastmcp"
    timeout: float | None = None
    allowed_schema: str | None = None
    read_only: bool = True
    table_prefix: str | None = None
    client_timeout_grace: float = 5.0


class SafeSqlExecutor:
    """Композиционная обертка: валидация SQL, установка statement_timeout/search_path, делегирование выполнения."""

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
        """Валидация запроса, затем выполнение через делегата в транзакции с statement_timeout и search_path.

        Args:
            query: SQL для выполнения.
            params: Необязательные параметры (будут встроены в запрос перед выполнением).
            readonly: Игнорируется; используется self._config.read_only.

        Returns:
            Строки или None для операторов без результата.

        Raises:
            QueryTimeoutError: Postgres отменил запрос по statement_timeout либо сработала клиентская страховка.
            QueryCancelledError: Postgres отменил запрос по другой причине.
        """
        query = self.render(query, params) if params else f"/* {self._config.query_tag} */ {query}"
        self._validator.validate(query)
        query = self._with_session_settings(query)
        if self._config.timeout is None:
            return await self._run(query)
        try:
            async with asyncio.timeout(self._config.timeout + self._config.client_timeout_grace):
                return await self._run(query)
        except TimeoutError as e:
            logger.warning(
                "Client-side timeout after %ss: %s...",
                self._config.timeout,
                query[:100],
            )
            raise QueryTimeoutError(self._config.timeout) from e

    async def _run(self, query: str) -> list[RowResult] | None:
        """Выполнить через делегата; отмену по statement_timeout превратить в QueryTimeoutError.

        Любая другая отмена (pg_cancel_backend, запрос пользователя) становится QueryCancelledError,
        чтобы не выдавать её за таймаут.
        """
        try:
            return cast(
                "list[RowResult] | None",
                await self._delegate.execute(query, params=None, readonly=self._config.read_only),
            )
        except QueryCanceled as e:
            reason = e.diag.message_primary or ""
            if _STATEMENT_TIMEOUT_MARKER in reason:
                logger.warning(
                    "Postgres cancelled the statement (statement_timeout=%ss): %s...",
                    self._config.timeout,
                    query[:100],
                )
                raise QueryTimeoutError(self._config.timeout or 0.0) from e
            logger.warning("Postgres cancelled the statement (%s): %s...", reason or "no reason", query[:100])
            raise QueryCancelledError from e

    def _with_session_settings(self, query: str) -> str:
        """Добавить SET LOCAL statement_timeout и search_path; порядок важен для читаемости логов."""
        prefix: list[str] = []
        if self._config.timeout is not None:
            prefix.append(f"SET LOCAL statement_timeout = {int(self._config.timeout * MS_PER_SECOND)};")
        if self._config.allowed_schema:
            prefix.append(f"SET LOCAL search_path = {self._config.allowed_schema};")
        return " ".join([*prefix, query])

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
