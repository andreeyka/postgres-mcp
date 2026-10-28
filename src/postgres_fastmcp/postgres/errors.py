"""Безопасное представление ошибок PostgreSQL на внешней границе сервера."""

import re

from psycopg import Error

from postgres_fastmcp.shared.errors import UserFacingError


_URI = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"<>]+", re.IGNORECASE)
_PARAMETER = re.compile(
    r"\b(host|hostaddr|port|dbname|database|user|password|passfile|sslpassword|sslkey|"
    r"service|servicefile|options|token|access_token|refresh_token|api_key|apikey|secret|client_secret)"
    r"\s*=\s*(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|(?:\\.|[^\s\"'\\])+)",
    re.IGNORECASE,
)
_AUTHORIZATION = re.compile(r"\b(Bearer|Basic)\s+[a-z0-9._~+/=-]+", re.IGNORECASE)
_REDACTED = "[REDACTED]"
_FALLBACK = "Database operation failed."


def _safe_message(message: str) -> str:
    """Удалить URI, параметры подключения и значения учётных данных из основного сообщения."""
    message = _URI.sub(_REDACTED, message)
    message = _PARAMETER.sub(lambda match: f"{match[1]}={_REDACTED}", message)
    return _AUTHORIZATION.sub(_REDACTED, message)


class PostgresQueryError(UserFacingError):
    """Ошибка БД с SQLSTATE и очищенным основным сообщением, доступная MCP-клиенту."""

    def __init__(self, error: Error) -> None:
        """Сформировать сообщение без DETAIL, CONTEXT, SQL и текста исключения драйвера.

        Args:
            error: Исходная ошибка psycopg с диагностикой PostgreSQL.
        """
        self.sqlstate = error.sqlstate
        if self.sqlstate is None:
            message = f"{_FALLBACK} PostgreSQL diagnostics are unavailable."
        else:
            primary = (
                "Database connection failed."
                if self.sqlstate.startswith(("08", "28"))
                else _safe_message(error.diag.message_primary or _FALLBACK)
            )
            message = f"PostgreSQL error [{self.sqlstate}]: {primary}"
        super().__init__(message)
