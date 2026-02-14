"""Пользовательские классы исключений для приложения.

Все ошибки наследуются от BaseApplicationError. Сообщения об ошибках на английском
(согласовано с инструментами, поиском и операциями). См. правила обработки ошибок в AGENTS.md.
"""


class BaseApplicationError(Exception):
    """Базовый класс для всех ошибок приложения."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением об ошибке.

        Args:
            message: Текст сообщения.
        """
        super().__init__(message)
        self.message = message


class SchemaAccessError(BaseApplicationError):
    """Доступ к запрашиваемой схеме не разрешен (например, роль пользователя или непубличная схема)."""

    def __init__(self, schema_name: str) -> None:
        """Инициализация с именем схемы.

        Args:
            schema_name: Имя схемы, доступ к которой запрещён.
        """
        message = f"Access to schema '{schema_name}' is not allowed. Only 'public' schema is permitted."
        super().__init__(message)
        self.schema_name = schema_name


class UnsupportedObjectTypeError(BaseApplicationError):
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


class ExplainAnalyzeWithHypotheticalError(BaseApplicationError):
    """Нельзя использовать analyze и гипотетические индексы вместе."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Нельзя использовать analyze и гипотетические индексы вместе.")


class EmptyQueriesError(BaseApplicationError):
    """Пустой список запросов, где требуется хотя бы один."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Пожалуйста, предоставьте непустой список запросов для анализа.")


class QueriesLimitError(BaseApplicationError):
    """Слишком много запросов в списке (превышен допустимый лимит)."""

    def __init__(self, limit: int) -> None:
        """Инициализация с лимитом.

        Args:
            limit: Допустимое максимальное количество запросов.
        """
        message = f"Пожалуйста, предоставьте список не более чем из {limit} запросов для анализа."
        super().__init__(message)
        self.limit = limit


class ContextRequiredError(BaseApplicationError):
    """Контекст требуется для этой операции (например, оптимизация LLM)."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Контекст требуется для метода оптимизации LLM.")


class InvalidSortCriteriaError(BaseApplicationError):
    """Неверный критерий сортировки для топ-запросов."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Неверный критерий сортировки. Пожалуйста, используйте 'resources', 'mean_time' или 'total_time'.")


class SqlExecutionError(BaseApplicationError):
    """Ошибка выполнения SQL (например, нет результатов или ошибка драйвера)."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением.

        Args:
            message: Описание ошибки.
        """
        super().__init__(message)


class HypopgNotInstalledError(BaseApplicationError):
    """Расширение HypoPG не установлено или недоступно."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением.

        Args:
            message: Текст результата проверки установки HypoPG.
        """
        super().__init__(message)


class InvalidHealthTypeError(BaseApplicationError):
    """Предоставлен(ы) неверный тип(ы) проверки состояния."""

    def __init__(self, health_type: str, valid_values: str) -> None:
        """Инициализация.

        Args:
            health_type: Переданное значение.
            valid_values: Список допустимых значений.
        """
        message = (
            f"Предоставлен(ы) неверный тип(ы) проверки состояния: '{health_type}'. "
            f"Допустимые значения: {valid_values}. "
            "Пожалуйста, попробуйте снова с comma-разделенным списком допустимых типов проверки состояния."
        )
        super().__init__(message)
        self.health_type = health_type
        self.valid_values = valid_values
