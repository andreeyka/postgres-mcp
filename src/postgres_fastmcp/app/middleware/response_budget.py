"""Бюджет ответа тула в токенах: слишком большой ответ заменяется ошибкой «уточните запрос».

В отличие от обрезки, агент не получает оборванную таблицу или битый JSON,
а узнаёт, что запрос надо сузить.
"""

import math

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from postgres_fastmcp.shared.errors import ResponseTooLargeError


# Грубая оценка без токенайзера: JSON и латиница ~3.5-4 байта на токен, кириллица ~2-3.
# Делитель 3 слегка завышает оценку: лимит срабатывает раньше, а не позже.
BYTES_PER_TOKEN = 3


def estimate_tokens(result: ToolResult) -> int:
    """Оценить размер ответа тула в токенах по тексту, который читает модель.

    structured_content не учитывается: в режиме json его копия уже лежит в тексте.

    Args:
        result: Результат тула.

    Returns:
        Оценка числа токенов (округление вверх).
    """
    size = sum(len(block.text.encode()) for block in result.content if isinstance(block, TextContent))
    return math.ceil(size / BYTES_PER_TOKEN)


class ResponseBudgetMiddleware(Middleware):
    """Отклоняет ответы тулов больше лимита токенов (ServerSettings.response_max_tokens)."""

    def __init__(self, max_tokens: int) -> None:
        """Инициализировать middleware.

        Args:
            max_tokens: Предел ответа тула в токенах.
        """
        self._max_tokens = max_tokens

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Выполнить тул и проверить размер ответа.

        Args:
            context: Контекст вызова.
            call_next: Следующий обработчик цепочки.

        Returns:
            Результат тула, если он помещается в лимит.

        Raises:
            ResponseTooLargeError: Если ответ больше лимита токенов.
        """
        result = await call_next(context)
        tokens = estimate_tokens(result)
        if tokens > self._max_tokens:
            raise ResponseTooLargeError(tokens, self._max_tokens)
        return result
