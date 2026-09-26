"""Пользовательские классы исключений для приложения.

Все ошибки наследуются от BaseApplicationError. Сообщения об ошибках на английском
(согласовано с инструментами, поиском и операциями). См. правила обработки ошибок в AGENTS.md.
"""

from fastmcp.exceptions import ToolError


class BaseApplicationError(Exception):
    """Базовый класс для всех ошибок приложения."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением об ошибке.

        Args:
            message: Текст сообщения.
        """
        super().__init__(message)
        self.message = message


class UserFacingError(ToolError):
    """Базовый класс для ошибок, сообщение которых безопасно показывать MCP-клиенту."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением об ошибке.

        Args:
            message: Текст сообщения.
        """
        super().__init__(message)
        self.message = message


class ConnectionNotEstablishedError(BaseApplicationError):
    """Подключение к БД не установлено (не заданы conn или engine_url)."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Connection not established. Either conn or engine_url must be provided.")


class SchemaAccessError(UserFacingError):
    """Доступ к запрашиваемой схеме не разрешен (например, роль пользователя или непубличная схема)."""

    def __init__(self, schema_name: str) -> None:
        """Инициализация с именем схемы.

        Args:
            schema_name: Имя схемы, доступ к которой запрещён.
        """
        message = f"Access to schema '{schema_name}' is not allowed. Only 'public' schema is permitted."
        super().__init__(message)
        self.schema_name = schema_name


class SchemaNotAllowedError(UserFacingError):
    """Доступ к указанной схеме запрещён; разрешена только заданная схема (валидация SQL)."""

    def __init__(self, schema_name: str, allowed_schema: str) -> None:
        """Инициализация с именем схемы и разрешённой схемой.

        Args:
            schema_name: Имя схемы, доступ к которой запрещён.
            allowed_schema: Единственная разрешённая схема.
        """
        message = f"Access to schema '{schema_name}' is not allowed. Only '{allowed_schema}' schema is permitted."
        super().__init__(message)
        self.schema_name = schema_name
        self.allowed_schema = allowed_schema


class TablePrefixAccessError(UserFacingError):
    """Доступ к таблице запрещён: имя не соответствует обязательному префиксу (валидация SQL)."""

    def __init__(self, table_name: str, table_prefix: str) -> None:
        """Инициализация с именем таблицы и требуемым префиксом.

        Args:
            table_name: Имя таблицы, доступ к которой запрещён.
            table_prefix: Требуемый префикс имени таблицы.
        """
        message = (
            f"Access to table '{table_name}' is not allowed. "
            f"Only tables with names starting with '{table_prefix}' are permitted."
        )
        super().__init__(message)
        self.table_name = table_name
        self.table_prefix = table_prefix


class SchemataTableAccessError(UserFacingError):
    """Доступ к information_schema.schemata в пользовательском режиме запрещён."""

    def __init__(self, schema_name: str, table_name: str) -> None:
        """Инициализация с именем схемы и таблицы.

        Args:
            schema_name: Имя схемы (например, information_schema).
            table_name: Имя таблицы (например, schemata).
        """
        message = (
            f"Access to '{schema_name}.{table_name}' is not allowed with access_mode=basic. "
            "Use the list_schemas tool instead (available with access_mode=full)."
        )
        super().__init__(message)
        self.schema_name = schema_name
        self.table_name = table_name


class SqlParseError(UserFacingError):
    """Не удалось разобрать SQL-запрос (синтаксическая ошибка)."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Failed to parse SQL statement")


class StatementTypeNotAllowedError(UserFacingError):
    """Тип оператора не разрешён при текущей политике (read-only или DML)."""

    def __init__(self, *, read_only: bool, stmt_type_name: str) -> None:
        """Инициализация с режимом и именем типа оператора.

        Args:
            read_only: Режим только чтение.
            stmt_type_name: Имя типа оператора, который не разрешён.
        """
        if read_only:
            message = (
                "Only SELECT, EXPLAIN, SHOW and other read-only statements are allowed "
                f"in read-only mode. Received: {stmt_type_name}"
            )
        else:
            message = (
                "Only SELECT, INSERT, UPDATE, DELETE, EXPLAIN, SHOW and CREATE EXTENSION "
                "(hypopg, pg_stat_statements) are allowed. "
                "DDL operations (CREATE, DROP, ALTER), VACUUM and ANALYZE are not allowed. "
                f"Received: {stmt_type_name}"
            )
        super().__init__(message)
        self.read_only = read_only
        self.stmt_type_name = stmt_type_name


class DdlNotAllowedError(UserFacingError):
    """DDL-операции (CREATE/DROP/ALTER) не разрешены при валидации запроса."""

    def __init__(self, stmt_type_name: str) -> None:
        """Инициализация с именем типа оператора.

        Args:
            stmt_type_name: Имя типа DDL-оператора.
        """
        message = f"DDL operations are not allowed. Received: {stmt_type_name}"
        super().__init__(message)
        self.stmt_type_name = stmt_type_name


class DisallowedNodeTypeError(UserFacingError):
    """Тип узла AST не разрешён при валидации запроса."""

    def __init__(self, node_type: type) -> None:
        """Инициализация с типом узла.

        Args:
            node_type: Тип узла AST, который не разрешён.
        """
        message = f"Node type {node_type} is not allowed"
        super().__init__(message)
        self.node_type = node_type


class LikePatternNotConstantError(UserFacingError):
    """В LIKE/ILIKE выражении паттерн должен быть константной строкой."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("LIKE pattern must be a constant string")


class FunctionNotAllowedError(UserFacingError):
    """Использование указанной функции в запросе не разрешено."""

    def __init__(self, func_name: str) -> None:
        """Инициализация с именем функции.

        Args:
            func_name: Имя функции, которая не разрешена.
        """
        message = f"Function {func_name} is not allowed"
        super().__init__(message)
        self.func_name = func_name


class LockingClauseProhibitedError(UserFacingError):
    """Использование блокирующих предложений в SELECT запрещено."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Locking clause on select is prohibited")


class ExplainAnalyzeNotSupportedError(UserFacingError):
    """EXPLAIN ANALYZE не поддерживается при валидации запроса."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("EXPLAIN ANALYZE is not supported")


class CreateExtensionNotSupportedError(UserFacingError):
    """Создание указанного расширения не разрешено."""

    def __init__(self, extname: str) -> None:
        """Инициализация с именем расширения.

        Args:
            extname: Имя расширения, которое не разрешено.
        """
        message = f"CREATE EXTENSION {extname} is not supported"
        super().__init__(message)
        self.extname = extname


class UnsupportedObjectTypeError(UserFacingError):
    """Запрашиваемый тип объекта не поддерживается."""

    def __init__(self, object_type: str) -> None:
        """Инициализация с типом объекта.

        Args:
            object_type: Неподдерживаемый тип объекта.
        """
        message = f"Unsupported object type: {object_type}"
        super().__init__(message)
        self.object_type = object_type


class ExplainPlanError(BaseApplicationError):
    """Ошибка при генерации или обработке плана EXPLAIN."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением.

        Args:
            message: Описание ошибки.
        """
        super().__init__(message)


class ExplainPlanExecutionError(ExplainPlanError):
    """Ошибка выполнения запроса EXPLAIN (ловится по типу в сервисе explain)."""

    def __init__(self, inner: BaseException) -> None:
        """Инициализация с исходным исключением.

        Args:
            inner: Исходное исключение, вызвавшее ошибку выполнения.
        """
        message = f"Error executing explain plan: {inner}"
        super().__init__(message)
        self.inner = inner


class ExplainAnalyzeWithHypotheticalError(UserFacingError):
    """Нельзя использовать analyze и гипотетические индексы вместе."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Нельзя использовать analyze и гипотетические индексы вместе.")


class EmptyQueriesError(UserFacingError):
    """Пустой список запросов, где требуется хотя бы один."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Пожалуйста, предоставьте непустой список запросов для анализа.")


class QueriesLimitError(UserFacingError):
    """Слишком много запросов в списке (превышен допустимый лимит)."""

    def __init__(self, limit: int) -> None:
        """Инициализация с лимитом.

        Args:
            limit: Допустимое максимальное количество запросов.
        """
        message = f"Пожалуйста, предоставьте список не более чем из {limit} запросов для анализа."
        super().__init__(message)
        self.limit = limit


class InvalidSortCriteriaError(UserFacingError):
    """Неверный критерий сортировки для топ-запросов."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__(
            "Неверный критерий сортировки. Пожалуйста, используйте 'resources', 'mean_time' или 'total_time'."
        )


class HypopgNotInstalledError(UserFacingError):
    """Расширение HypoPG не установлено или недоступно."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением.

        Args:
            message: Текст результата проверки установки HypoPG.
        """
        super().__init__(message)


class QueryTimeoutError(UserFacingError):
    """Выполнение запроса превысило заданный таймаут в режиме ограничения."""

    def __init__(self, timeout_seconds: float) -> None:
        """Инициализация с длительностью таймаута в секундах.

        Args:
            timeout_seconds: Таймаут в секундах, который был превышен.
        """
        message = (
            f"Query execution exceeded the timeout of {timeout_seconds} seconds in read_only mode. "
            "Consider simplifying the query or increasing the timeout."
        )
        super().__init__(message)
        self.timeout_seconds = timeout_seconds


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
