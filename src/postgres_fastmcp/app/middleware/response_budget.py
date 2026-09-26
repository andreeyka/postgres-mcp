"""Бюджет ответа тула в токенах: слишком большой ответ заменяется ошибкой «уточните запрос».

В отличие от обрезки, агент не получает оборванную таблицу или битый JSON,
а узнаёт, что запрос надо сузить.
"""

import logging
import math

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from postgres_fastmcp.shared.errors import ResponseTooLargeAfterWriteError, ResponseTooLargeError


# Грубая оценка без токенайзера: JSON и латиница ~3.5-4 байта на токен, кириллица ~2-3.
# Делитель 3 слегка завышает оценку: лимит срабатывает раньше, а не позже.
BYTES_PER_TOKEN = 3

logger = logging.getLogger(__name__)


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
            ResponseTooLargeAfterWriteError: Ответ больше лимита, а тул мог записать данные.
            ResponseTooLargeError: Если ответ больше лимита токенов.
        """
        result = await call_next(context)
        tokens = estimate_tokens(result)
        if tokens > self._max_tokens:
            if await _may_have_written(context):
                raise ResponseTooLargeAfterWriteError(tokens, self._max_tokens)
            raise ResponseTooLargeError(tokens, self._max_tokens)
        return result


async def _may_have_written(context: MiddlewareContext[mt.CallToolRequestParams]) -> bool:
    """Мог ли тул записать данные: read_only_hint=False в его аннотациях.

    Тул ищется на сервере по имени из запроса. Не нашли тул или аннотации — считаем,
    что записи не было, и отдаём обычную ошибку «уточните запрос».

    Тул без аннотаций считается read-only — это не консервативный выбор (консервативнее
    было бы считать его пишущим), но на этом сервере аннотирован каждый тул, так что
    на практике до этой ветки дело не доходит.

    Для версионированных тулов поиск по имени отдаёт самую старшую из включённых версий.

    Текст ошибки в ResponseTooLargeAfterWriteError ориентирован на SQL (упоминает SELECT
    и «statement») даже если оверсайз поймали у стороннего тула без отношения к SQL.
    """
    if context.fastmcp_context is None:
        return False
    try:
        tool = await context.fastmcp_context.fastmcp.get_tool(context.message.name)
    except Exception:
        logger.warning("Response budget: failed to resolve tool %s", context.message.name, exc_info=True)
        return False
    annotations = tool.annotations if tool is not None else None
    return annotations is not None and annotations.read_only_hint is False
