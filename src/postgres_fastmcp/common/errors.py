"""Custom exception classes for the application.

All errors inherit from BaseApplicationError. Error messages are in English
(consistent with tooling, search, and ops). See AGENTS.md Error Handling Rules.
"""


class BaseApplicationError(Exception):
    """Базовый класс для всех ошибок приложения."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением об ошибке.

        Args:
            message: Текст сообщения (на английском).
        """
        super().__init__(message)
        self.message = message


class SchemaAccessError(BaseApplicationError):
    """Access to the requested schema is not allowed (e.g. user role, non-public schema)."""

    def __init__(self, schema_name: str) -> None:
        """Инициализация с именем схемы.

        Args:
            schema_name: Имя схемы, доступ к которой запрещён.
        """
        message = f"Access to schema '{schema_name}' is not allowed. Only 'public' schema is permitted."
        super().__init__(message)
        self.schema_name = schema_name


class UnsupportedObjectTypeError(BaseApplicationError):
    """Requested object type is not supported."""

    def __init__(self, object_type: str) -> None:
        """Инициализация с типом объекта.

        Args:
            object_type: Неподдерживаемый тип объекта.
        """
        message = f"Unsupported object type: {object_type}"
        super().__init__(message)
        self.object_type = object_type


class ExplainPlanError(BaseApplicationError):
    """Error while generating or processing an EXPLAIN plan."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением.

        Args:
            message: Описание ошибки (на английском).
        """
        super().__init__(message)


class ExplainAnalyzeWithHypotheticalError(BaseApplicationError):
    """Cannot use analyze and hypothetical indexes together."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Cannot use analyze and hypothetical indexes together.")


class EmptyQueriesError(BaseApplicationError):
    """Empty list of queries provided where at least one is required."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Please provide a non-empty list of queries to analyze.")


class QueriesLimitError(BaseApplicationError):
    """Too many queries in the list (exceeds allowed limit)."""

    def __init__(self, limit: int) -> None:
        """Инициализация с лимитом.

        Args:
            limit: Допустимое максимальное количество запросов.
        """
        message = f"Please provide a list of up to {limit} queries to analyze."
        super().__init__(message)
        self.limit = limit


class ContextRequiredError(BaseApplicationError):
    """Context is required for this operation (e.g. LLM optimization)."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Context is required for LLM optimization method.")


class InvalidSortCriteriaError(BaseApplicationError):
    """Invalid sort criteria for top queries."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Invalid sort criteria. Please use 'resources' or 'mean_time' or 'total_time'.")


class SqlExecutionError(BaseApplicationError):
    """Error executing SQL (e.g. no results, driver error)."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением.

        Args:
            message: Описание ошибки (на английском).
        """
        super().__init__(message)


class HypopgNotInstalledError(BaseApplicationError):
    """HypoPG extension is not installed or not available."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением.

        Args:
            message: Текст от check_hypopg_installation_status.
        """
        super().__init__(message)


class InvalidHealthTypeError(BaseApplicationError):
    """Invalid health check type(s) provided."""

    def __init__(self, health_type: str, valid_values: str) -> None:
        """Инициализация.

        Args:
            health_type: Переданное значение.
            valid_values: Список допустимых значений.
        """
        message = (
            f"Invalid health types provided: '{health_type}'. "
            f"Valid values are: {valid_values}. "
            "Please try again with a comma-separated list of valid health types."
        )
        super().__init__(message)
        self.health_type = health_type
        self.valid_values = valid_values
