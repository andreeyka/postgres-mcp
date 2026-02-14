"""Порт для рендеринга параметризованного запроса в одну строку."""

from typing import Any, Protocol


class QueryTemplatePort(Protocol):
    """Протокол для подстановки параметров в шаблон запроса."""

    def render(self, query: str, params: list[Any]) -> str:
        """Возвращает строку запроса с встроенными параметрами (например, psycopg {} плейсхолдеры)."""
        ...
