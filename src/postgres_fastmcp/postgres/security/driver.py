"""Исполнитель безопасного SQL: валидация + statement_timeout + search_path вокруг делегирующего исполнителя."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from psycopg.errors import QueryCanceled
from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.postgres.models import RowResult, StatementResult
from postgres_fastmcp.postgres.ports import Precheck, PrecheckSqlDriverPort, StatementRunner
from postgres_fastmcp.postgres.security.plan_guard import PlanGuard
from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.errors import QueryCancelledError, QueryTimeoutError


logger = logging.getLogger(__name__)

MS_PER_SECOND = 1000

# Фрагмент diag.message_primary, по которому Postgres сообщает об отмене по statement_timeout
# ("canceling statement due to statement timeout"). Работает только при английском lc_messages,
# поэтому дополняется проверкой прошедшего времени в _is_statement_timeout.
_STATEMENT_TIMEOUT_MARKER = "statement timeout"

# Запас в секундах для клиентской страховки поверх statement_timeout; вынесен в константу,
# чтобы registry.py мог использовать то же значение без создания временного SafeSqlConfig.
CLIENT_TIMEOUT_GRACE_SECONDS = 5.0


def _is_statement_timeout(message_primary: str | None, *, elapsed: float, timeout: float | None) -> bool:
    """Отличить отмену по statement_timeout от прочих отмен (pg_cancel_backend, запрос пользователя).

    Timeout, если сообщение сервера содержит английский маркер ИЛИ прошло не меньше
    настроенного таймаута: раньше этого срока statement_timeout сработать не может, а текст
    сообщения зависит от lc_messages.
    """
    if _STATEMENT_TIMEOUT_MARKER in (message_primary or ""):
        return True
    return timeout is not None and elapsed >= timeout


@dataclass(frozen=True, slots=True)
class SafeSqlConfig:
    """Конфигурация SafeSqlExecutor.

    Attributes:
        query_tag: Тег добавляется к запросам для логирования/мониторинга.
        timeout: Таймаут выполнения в секундах; выставляется как statement_timeout в Postgres.
        allowed_schema: Разрешенная схема (например, 'public'); None означает все.
        read_only: Если True, только операторы чтения; если False, разрешен DML.
        table_prefix: Если задан вместе с allowed_schema, только таблицы с этим префиксом.
        plan_check: Проверять план запроса перед выполнением (PlanGuard); действует только вместе с allowed_schema.
        client_timeout_grace: Запас в секундах для клиентской страховки поверх statement_timeout.
            Обычно срабатывает Postgres; клиентский таймаут ловит зависшее соединение.
    """

    query_tag: str = "postgres-fastmcp"
    timeout: float | None = None
    allowed_schema: str | None = None
    read_only: bool = True
    table_prefix: str | None = None
    plan_check: bool = False
    client_timeout_grace: float = CLIENT_TIMEOUT_GRACE_SECONDS


class SafeSqlExecutor:
    """Композиционная обертка: валидация SQL, установка statement_timeout/search_path, делегирование выполнения."""

    def __init__(
        self,
        delegate: PrecheckSqlDriverPort,
        validator: QueryValidator,
        config: SafeSqlConfig,
    ) -> None:
        """Инициализация с делегирующим исполнителем, валидатором и конфигурацией.

        Args:
            delegate: Исполнитель без проверок (SqlExecutor): execute и execute_statement с precheck.
            validator: Валидатор, используемый для валидации каждого запроса перед выполнением.
            config: Конфигурация безопасного SQL (тег, таймаут, схема, read_only, префикс).
        """
        self._delegate = delegate
        self._validator = validator
        self._config = config
        # Проверка по плану — только для basic (allowed_schema задан); full и канал сервера её не получают.
        self._plan_check_schema = config.allowed_schema if config.plan_check else None

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
            PlanAccessError: plan_check в basic, план читает отношение или функцию вне разрешённого.
            PlanUnverifiableError: plan_check в basic, план нельзя проверить (нет плана или узел без имени).
        """
        return await self._guarded(query, params, self._delegate.execute)

    async def execute_statement(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,  # noqa: ARG002 — part of SqlDriverPort; effective value from config
    ) -> StatementResult:
        """То же, что execute, но со строками отдаёт тег команды Postgres ("UPDATE 3", "CREATE TABLE").

        Валидация, statement_timeout, search_path, клиентская страховка и разбор отмены — те же,
        что у execute: оба метода идут через _guarded.

        Raises:
            QueryTimeoutError: Postgres отменил запрос по statement_timeout либо сработала клиентская страховка.
            QueryCancelledError: Postgres отменил запрос по другой причине.
            PlanAccessError: plan_check в basic, план читает отношение или функцию вне разрешённого.
            PlanUnverifiableError: plan_check в basic, план нельзя проверить (нет плана или узел без имени).
        """
        return await self._guarded(query, params, self._delegate.execute_statement)

    async def _guarded[T](
        self,
        query: str,
        params: list[Any] | None,
        run: Callable[..., Awaitable[T]],
    ) -> T:
        """Тег, валидация, проверка по плану, SET LOCAL и клиентская страховка вокруг метода делегата run."""
        query = self.render(query, params) if params else f"/* {self._config.query_tag} */ {query}"
        self._validator.validate(query)
        if self._config.timeout is None:
            return await self._checked_run(query, run)
        try:
            async with asyncio.timeout(self._config.timeout + self._config.client_timeout_grace):
                return await self._checked_run(query, run)
        except TimeoutError as e:
            logger.warning(
                "Client-side timeout after %ss: %s...",
                self._config.timeout,
                query[:100],
            )
            raise QueryTimeoutError(self._config.timeout) from e

    async def _checked_run[T](self, query: str, run: Callable[..., Awaitable[T]]) -> T:
        """Выполнение с SET LOCAL через делегата; с plan_check — проверка по плану в той же транзакции.

        С plan_check делегат получает precheck: на том же соединении, после BEGIN, он один раз ставит
        SET LOCAL statement_timeout/search_path и строит план каждого оператора; оператор идёт без префикса
        и наследует настройки транзакции. AccessShareLock, взятый разбором, держится до конца транзакции:
        определение представления между проверкой и выполнением не меняется. Отказ проверки откатывает
        транзакцию, оператор не выполняется.

        Ограничение: PlanGuard строит планы всех операторов строки до выполнения первого, поэтому строка,
        где поздний оператор зависит от раннего (CREATE EXTENSION …; SELECT функция расширения), отклоняется
        ошибкой планирования.
        """
        allowed_schema = self._plan_check_schema
        if allowed_schema is None:
            return await self._run(self._with_session_settings(query), run, readonly=self._config.read_only)
        settings = self._session_settings()
        tag = self._config.query_tag
        table_prefix = self._config.table_prefix

        async def precheck(runner: StatementRunner) -> None:
            if settings:
                await runner(settings)

            async def explain(explain_sql: str) -> list[RowResult] | None:
                # Текст EXPLAIN — deparse pglast (standard_conforming_strings = on закрепляет SqlExecutor в BEGIN).
                return await runner(f"/* {tag} */ {explain_sql}")

            await PlanGuard(explain, allowed_schema=allowed_schema, table_prefix=table_prefix).check(query)

        return await self._run(query, run, readonly=self._config.read_only, precheck=precheck)

    async def _run[T](
        self,
        query: str,
        run: Callable[..., Awaitable[T]],
        *,
        readonly: bool,
        precheck: Precheck | None = None,
    ) -> T:
        """Выполнить через делегата; отмену по statement_timeout превратить в QueryTimeoutError.

        Любая другая отмена (pg_cancel_backend, запрос пользователя) становится QueryCancelledError,
        чтобы не выдавать её за таймаут. Отмена во время EXPLAIN проверки разбирается так же: они идут
        внутри того же вызова. Без precheck ключ делегату не передаётся.
        """
        started = monotonic()
        try:
            if precheck is None:
                return await run(query, params=None, readonly=readonly)
            return await run(query, params=None, readonly=readonly, precheck=precheck)
        except QueryCanceled as e:
            reason = e.diag.message_primary or ""
            if _is_statement_timeout(reason, elapsed=monotonic() - started, timeout=self._config.timeout):
                logger.warning(
                    "Postgres cancelled the statement (statement_timeout=%ss): %s...",
                    self._config.timeout,
                    query[:100],
                )
                raise QueryTimeoutError(self._config.timeout or 0.0) from e
            logger.warning("Postgres cancelled the statement (%s): %s...", reason or "no reason", query[:100])
            raise QueryCancelledError from e

    def _session_settings(self) -> str:
        """SET LOCAL statement_timeout и search_path одной строкой; порядок важен для читаемости логов."""
        prefix: list[str] = []
        if self._config.timeout is not None:
            prefix.append(f"SET LOCAL statement_timeout = {int(self._config.timeout * MS_PER_SECOND)};")
        if self._config.allowed_schema:
            prefix.append(f"SET LOCAL search_path = {self._config.allowed_schema};")
        return " ".join(prefix)

    def _with_session_settings(self, query: str) -> str:
        """Запрос с префиксом _session_settings (без префикса, если настраивать нечего)."""
        settings = self._session_settings()
        return f"{settings} {query}" if settings else query

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
