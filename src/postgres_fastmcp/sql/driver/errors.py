"""Ошибки SQL-драйвера и подключения.

Все ошибки наследуются от BaseApplicationError по правилам обработки ошибок проекта.
"""

from postgres_fastmcp.common.errors import BaseApplicationError


class ConnectionFailedError(BaseApplicationError):
    """Сбой подключения к базе данных или инициализации пула."""

    def __init__(self, error_details: str | None) -> None:
        """Инициализация с деталями ошибки подключения (например, обфусцированное сообщение).

        Args:
            error_details: Детали ошибки подключения (пароли обфусцированы).
        """
        message = f"Connection attempt failed: {error_details}"
        super().__init__(message)
        self.error_details = error_details
