"""Порты (протоколы) слоя SQL: исполнитель и шаблонизатор запросов.

Живут в слое ``sql`` намеренно: их реализуют ``SqlExecutor``/``SafeSqlExecutor``,
а зависят от них потребители того же слоя (``sql.params.replacer``). Это сохраняет
направление зависимостей ``services -> sql`` без обратной инверсии.
"""

from typing import Any, Protocol

from postgres_fastmcp.sql.models import RowResult


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
