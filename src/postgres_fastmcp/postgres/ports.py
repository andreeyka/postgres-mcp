"""Порты (протоколы) слоя SQL: исполнитель и шаблонизатор запросов.

Живут в слое ``sql`` намеренно: их реализуют ``SqlExecutor``/``SafeSqlExecutor``,
а зависят от них потребители того же слоя (``sql.params.replacer``). Это сохраняет
направление зависимостей ``services -> sql`` без обратной инверсии.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from postgres_fastmcp.postgres.models import RowResult, StatementResult


class QueryExecutorPort(Protocol):
    """Протокол для выполнения SQL запросов (только чтение или чтение-запись)."""

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
    ) -> list[RowResult] | None:
        """Выполнение запроса и возвращение строк или None для операторов без результата."""
        ...


class QueryTemplatePort(Protocol):
    """Протокол для рендеринга параметризованного запроса в одну строку."""

    def render(self, query: str, params: list[Any]) -> str:
        """Рендер запроса с встроенными параметрами (например, psycopg {} плейсхолдеры)."""
        ...


class SqlDriverPort(QueryExecutorPort, QueryTemplatePort, Protocol):
    """Полный контракт SQL-драйвера: выполнение запросов и рендеринг параметров.

    Единственный тип, которым сервисы (домены) должны типизировать драйвер —
    конкретные реализации (``SqlExecutor``, ``SafeSqlExecutor``) остаются
    деталью слоя ``sql``.
    """

    async def execute_statement(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
    ) -> StatementResult:
        """Выполнение запроса: строки и тег команды Postgres (для execute_sql)."""
        ...


# Выполняет одну строку SQL на курсоре текущей транзакции и возвращает строки её последнего результата
# (None — у результата нет строк, например SET).
StatementRunner = Callable[[str], Awaitable[list[RowResult] | None]]

# Предварительные запросы в транзакции оператора (проверка по плану): получают StatementRunner;
# исключение отменяет оператор и откатывает транзакцию.
Precheck = Callable[[StatementRunner], Awaitable[None]]


class PrecheckSqlDriverPort(SqlDriverPort, Protocol):
    """SQL-драйвер: предварительные запросы в той же транзакции и на том же соединении, что и оператор.

    Отдельный протокол, а не параметр SqlDriverPort: его реализует только исполнитель без проверок
    (SqlExecutor). SafeSqlExecutor сам принимать чужой precheck не должен, а домены и их тестовые
    двойники остаются на SqlDriverPort.
    """

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
        precheck: Precheck | None = None,
    ) -> list[RowResult] | None:
        """Выполнение запроса (после precheck, если он задан) и возвращение строк или None."""
        ...

    async def execute_statement(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
        precheck: Precheck | None = None,
    ) -> StatementResult:
        """Выполнение запроса (после precheck, если он задан): строки и тег команды Postgres."""
        ...
