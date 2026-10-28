"""Преобразование ошибок БД перед маскированием внутренних ошибок FastMCP."""

from collections.abc import Awaitable, Callable
from functools import wraps

from psycopg import Error

from postgres_fastmcp.postgres.errors import PostgresQueryError
from postgres_fastmcp.shared.errors import ExplainPlanExecutionError


def expose_database_errors[**P, R](function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Обернуть тул, сохранив сигнатуру и внутренние обработчики psycopg.

    Args:
        function: Асинхронный метод инструмента.

    Returns:
        Метод с безопасным сообщением об ошибке БД для MCP-клиента.
    """

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            result = await function(*args, **kwargs)
        except Error as error:
            raise PostgresQueryError(error) from error
        except ExplainPlanExecutionError as error:
            if isinstance(error.inner, Error):
                raise PostgresQueryError(error.inner) from error
            raise
        else:
            return result

    return wrapped
