"""Пользовательские классы исключений для приложения.

Все ошибки наследуются от BaseApplicationError. Сообщения об ошибках на английском
(согласовано с инструментами, поиском и операциями). См. правила обработки ошибок в AGENTS.md.
"""

import difflib
from collections.abc import Sequence
from typing import get_args

from fastmcp.exceptions import ToolError

from postgres_fastmcp.shared.enums import ObjectType, TopQueriesSortBy


def _did_you_mean(value: str, allowed: Sequence[str]) -> str:
    """Подсказка ближайшего допустимого значения (пустая строка, если похожих нет)."""
    matches = difflib.get_close_matches(value.strip().lower(), allowed, n=1)
    return f" Did you mean '{matches[0]}'?" if matches else ""


def _one_of(allowed: Sequence[str]) -> str:
    """Список допустимых значений для текста ошибки: 'a', 'b', 'c'."""
    return ", ".join(f"'{item}'" for item in allowed)


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


class SystemRelationAccessError(UserFacingError):
    """Доступ к системному отношению (pg_*, _pg_*) в basic запрещён (валидация SQL)."""

    def __init__(self, relation: str) -> None:
        """Инициализация с именем отношения.

        Args:
            relation: Имя системного отношения из запроса.
        """
        message = (
            f"Access to system relation '{relation}' is not allowed in basic mode. "
            "Use list_objects and get_object_details to inspect tables in 'public'."
        )
        super().__init__(message)
        self.relation = relation


class ShowParameterNotAllowedError(UserFacingError):
    """SHOW параметра вне разрешённого списка basic (валидация SQL)."""

    def __init__(self, name: str, allowed: Sequence[str]) -> None:
        """Инициализация с именем параметра и разрешённым списком.

        Args:
            name: Имя параметра из SHOW.
            allowed: Параметры, которые basic разрешает читать.
        """
        message = f"SHOW {name} is not allowed in basic mode. Allowed parameters: {', '.join(sorted(allowed))}."
        super().__init__(message)
        self.name = name


class TypeNotAllowedError(UserFacingError):
    """Тип, резолвящий имена объектов (reg*, aclitem), в basic запрещён в любой позиции (валидация SQL)."""

    def __init__(self, type_name: str) -> None:
        """Инициализация с именем типа.

        Args:
            type_name: Имя типа из запроса (приведение, колонка табличной функции, аргумент PREPARE).
        """
        message = f"Type {type_name} is not allowed in basic mode. Rewrite the query without object identifier types."
        super().__init__(message)
        self.type_name = type_name


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


class PlanAccessError(UserFacingError):
    """План запроса basic читает отношение или функцию вне разрешённого (проверка по плану, plan_check)."""

    def __init__(self, kind: str, qualified_name: str) -> None:
        """Инициализация с видом объекта и его полным именем.

        Args:
            kind: Вид объекта из плана: relation или function.
            qualified_name: Имя со схемой из плана (schema.name).
        """
        message = (
            f"Access to {kind} '{qualified_name}' is not allowed in basic mode: the query reaches it through "
            "a view, rule or function. Only tables in 'public' are permitted."
        )
        super().__init__(message)
        self.kind = kind
        self.qualified_name = qualified_name


class SqlParseError(UserFacingError):
    """Не удалось разобрать SQL-запрос (синтаксическая ошибка)."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Failed to parse SQL statement. Check the SQL syntax and send a single valid statement.")


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
        message = (
            f"DDL operations are not allowed. Received: {stmt_type_name}. "
            "Use SELECT to read data; schema changes must be made outside this server."
        )
        super().__init__(message)
        self.stmt_type_name = stmt_type_name


class DisallowedNodeTypeError(UserFacingError):
    """Тип узла AST не разрешён при валидации запроса."""

    def __init__(self, node_type: type) -> None:
        """Инициализация с типом узла.

        Args:
            node_type: Тип узла AST, который не разрешён.
        """
        message = f"Node type {node_type.__name__} is not allowed. Rewrite the query without this SQL construct."
        super().__init__(message)
        self.node_type = node_type


class LikePatternNotConstantError(UserFacingError):
    """В LIKE/ILIKE выражении паттерн должен быть константной строкой."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__(
            "LIKE pattern must be a constant string. Put the pattern into a string literal, e.g. LIKE 'abc%'."
        )


class FunctionNotAllowedError(UserFacingError):
    """Использование указанной функции в запросе не разрешено."""

    def __init__(self, func_name: str) -> None:
        """Инициализация с именем функции.

        Args:
            func_name: Имя функции, которая не разрешена.
        """
        message = f"Function {func_name} is not allowed. Rewrite the query without this function."
        super().__init__(message)
        self.func_name = func_name


class LockingClauseProhibitedError(UserFacingError):
    """Использование блокирующих предложений в SELECT запрещено."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("Locking clause on select is prohibited. Remove FOR UPDATE / FOR SHARE from the query.")


class ExplainAnalyzeNotSupportedError(UserFacingError):
    """EXPLAIN ANALYZE не поддерживается при валидации запроса."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("EXPLAIN ANALYZE is not supported. Use the explain_query tool with analyze=true instead.")


class CreateExtensionNotSupportedError(UserFacingError):
    """Создание указанного расширения не разрешено."""

    def __init__(self, extname: str, option: str | None = None) -> None:
        """Инициализация с именем расширения и, при необходимости, запрещённой опцией.

        Args:
            extname: Имя расширения, которое не разрешено.
            option: Запрещённая опция CREATE EXTENSION (например, SCHEMA или CASCADE).
        """
        if option is None:
            message = (
                f"CREATE EXTENSION {extname} is not supported. "
                "Only hypopg and pg_stat_statements can be created, and only with write_mode enabled."
            )
        else:
            message = (
                f"CREATE EXTENSION {extname} with the {option} option is not supported. "
                f"Run CREATE EXTENSION {extname} without {option}."
            )
        super().__init__(message)
        self.extname = extname
        self.option = option


class UnsupportedObjectTypeError(UserFacingError):
    """Запрашиваемый тип объекта не поддерживается."""

    def __init__(self, object_type: str) -> None:
        """Инициализация с типом объекта.

        Args:
            object_type: Неподдерживаемый тип объекта.
        """
        allowed = get_args(ObjectType)
        message = (
            f"Unsupported object type: '{object_type}'.{_did_you_mean(object_type, allowed)} "
            f"Use one of: {_one_of(allowed)}."
        )
        super().__init__(message)
        self.object_type = object_type


# Для ObjectNotFoundError: таблица <-> представление
_OTHER_RELATION_TYPE = {"table": "view", "view": "table"}


class ObjectNotFoundError(UserFacingError):
    """Объект каталога не найден: get_object_details не должен выглядеть как успешный поиск."""

    def __init__(self, schema_name: str, object_name: str, object_type: str) -> None:
        """Инициализация со схемой, именем и типом объекта.

        Args:
            schema_name: Имя схемы.
            object_name: Имя объекта.
            object_type: Тип объекта (table, view, sequence, extension).
        """
        # Расширения не принадлежат схеме: схему в тексте не показываем
        qualified = object_name if object_type == "extension" else f"{schema_name}.{object_name}"
        # Таблицу и представление легко перепутать (по умолчанию object_type="table"): подсказываем другой тип
        other_type = _OTHER_RELATION_TYPE.get(object_type)
        if other_type is not None:
            hint = f'If it is a {other_type}, retry with object_type="{other_type}"; use list_objects'
        else:
            hint = "Use list_objects"
        super().__init__(f"Object not found: {qualified} ({object_type}). {hint} to see existing objects.")
        self.schema_name = schema_name
        self.object_name = object_name
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
        super().__init__(
            "analyze=true cannot be combined with hypothetical_indexes. "
            "Call explain_query twice: once with analyze=true, once with hypothetical_indexes."
        )


class EmptyQueriesError(UserFacingError):
    """Пустой список запросов, где требуется хотя бы один."""

    def __init__(self) -> None:
        """Инициализация."""
        super().__init__("The queries list is empty. Pass at least one SQL query to analyze.")


class QueriesLimitError(UserFacingError):
    """Слишком много запросов в списке (превышен допустимый лимит)."""

    def __init__(self, limit: int) -> None:
        """Инициализация с лимитом.

        Args:
            limit: Допустимое максимальное количество запросов.
        """
        message = (
            f"Too many queries: at most {limit} can be analyzed in one call. "
            "Split the list into several calls, or use analyze_workload_indexes for the whole workload."
        )
        super().__init__(message)
        self.limit = limit


class InvalidSortCriteriaError(UserFacingError):
    """Неверный критерий сортировки для топ-запросов."""

    def __init__(self, sort_by: str) -> None:
        """Инициализация с отклонённым значением.

        Args:
            sort_by: Значение sort_by, которое не удалось распознать.
        """
        allowed = get_args(TopQueriesSortBy)
        message = f"Invalid sort_by: '{sort_by}'.{_did_you_mean(sort_by, allowed)} Use one of: {_one_of(allowed)}."
        super().__init__(message)
        self.sort_by = sort_by


class InvalidHealthTypeError(UserFacingError):
    """Неизвестный тип health-проверки в health_type."""

    def __init__(self, health_type: str, allowed: Sequence[str]) -> None:
        """Инициализация с отклонённым значением и допустимыми типами.

        Args:
            health_type: Значение, которое не удалось распознать.
            allowed: Допустимые типы проверок (HealthType живёт в домене, shared его не импортирует).
        """
        message = (
            f"Invalid health_type: '{health_type}'.{_did_you_mean(health_type, allowed)} "
            f"Use one or more of: {_one_of(allowed)}, e.g. 'index,vacuum'."
        )
        super().__init__(message)
        self.health_type = health_type


class InvalidOutputFormatError(UserFacingError):
    """Неизвестный формат вывода в параметре output."""

    def __init__(self, output: str) -> None:
        """Инициализация с отклонённым значением.

        Args:
            output: Значение output, которое не удалось распознать.
        """
        allowed = ("table", "json")
        message = f"Invalid output: '{output}'.{_did_you_mean(output, allowed)} Use one of: {_one_of(allowed)}."
        super().__init__(message)
        self.output = output


class PgStatStatementsNotInstalledError(UserFacingError):
    """Расширение pg_stat_statements не установлено: топ запросов недоступен."""

    def __init__(self) -> None:
        """Инициализация с фиксированным сообщением и подсказкой по установке."""
        super().__init__(
            "The pg_stat_statements extension is not installed or not preloaded, "
            "so query statistics are unavailable. "
            "Ask a database administrator to add pg_stat_statements to shared_preload_libraries "
            "and run CREATE EXTENSION pg_stat_statements."
        )


class ExtensionStatusUnavailableError(UserFacingError):
    """Каталог расширений не ответил: статус расширения неизвестен (не путать с «не установлено»)."""

    def __init__(self, extension_name: str, reason: str) -> None:
        """Инициализация с именем расширения и причиной сбоя каталога.

        Args:
            extension_name: Имя расширения, статус которого не удалось проверить.
            reason: Описание сбоя каталога (английский текст для агента).
        """
        super().__init__(
            f"Could not check whether the {extension_name} extension is installed. {reason} "
            "Also check the database role's permissions and the connection, then retry."
        )
        self.extension_name = extension_name
        self.reason = reason


class UnsupportedServerVersionError(UserFacingError):
    """Возможность недоступна на версии PostgreSQL сервера."""

    def __init__(self, feature: str, min_version: int, actual: int, *, hint: str) -> None:
        """Инициализация с требуемой и фактической версией.

        Args:
            feature: Что недоступно, например "sort_by='resources'".
            min_version: Минимальная мажорная версия PostgreSQL.
            actual: Мажорная версия сервера.
            hint: Что сделать вместо этого (английский текст для агента).
        """
        super().__init__(
            f"{feature} requires PostgreSQL {min_version} or newer, but the server runs PostgreSQL {actual}. {hint}"
        )
        self.feature = feature
        self.min_version = min_version
        self.actual = actual


class HypopgNotInstalledError(UserFacingError):
    """Расширение HypoPG не установлено или недоступно."""

    def __init__(self, message: str) -> None:
        """Инициализация с сообщением.

        Args:
            message: Текст результата проверки установки HypoPG.
        """
        super().__init__(message)


class QueryTimeoutError(UserFacingError):
    """Выполнение запроса превысило заданный таймаут (statement_timeout или клиентская страховка)."""

    def __init__(self, timeout_seconds: float) -> None:
        """Инициализация с длительностью таймаута в секундах.

        Args:
            timeout_seconds: Таймаут в секундах, который был превышен.
        """
        message = (
            f"Query execution exceeded the timeout of {timeout_seconds} seconds. "
            "Consider simplifying the query or increasing the timeout."
        )
        super().__init__(message)
        self.timeout_seconds = timeout_seconds


class QueryCancelledError(UserFacingError):
    """Сервер отменил запрос не по statement_timeout (pg_cancel_backend, запрос пользователя)."""

    def __init__(self) -> None:
        """Инициализация с фиксированным сообщением."""
        super().__init__("The query was cancelled by the server before it completed.")


class ResponseTooLargeError(UserFacingError):
    """Ответ тула больше бюджета токенов: агенту нужно сузить запрос."""

    def __init__(self, tokens: int, max_tokens: int) -> None:
        """Инициализация с оценкой размера ответа и лимитом.

        Args:
            tokens: Оценка размера ответа в токенах.
            max_tokens: Лимит ответа в токенах.
        """
        message = (
            f"Response is too large: ~{tokens} tokens, the limit is {max_tokens}. Refine the request: "
            "add WHERE or LIMIT, select only the needed columns, aggregate (count, group by), "
            "or narrow the schema/object filter."
        )
        self._init(message, tokens, max_tokens)

    def _init(self, message: str, tokens: int, max_tokens: int) -> None:
        """Общая инициализация для подклассов с другим текстом."""
        UserFacingError.__init__(self, message)
        self.tokens = tokens
        self.max_tokens = max_tokens


class ResponseTooLargeAfterWriteError(ResponseTooLargeError):
    """Ответ пишущего тула больше бюджета: изменения уже применены, повтор запишет их дважды."""

    def __init__(self, tokens: int, max_tokens: int) -> None:
        """Инициализация с оценкой размера ответа и лимитом.

        Args:
            tokens: Оценка размера ответа в токенах.
            max_tokens: Лимит ответа в токенах.
        """
        message = (
            f"Response is too large: ~{tokens} tokens, the limit is {max_tokens}. "
            "If the statement modified data, its changes are already applied — do not re-run it; "
            "query the affected rows with a narrower SELECT instead. "
            "Otherwise refine the request: add WHERE or LIMIT, select only the needed columns, "
            "aggregate (count, group by)."
        )
        self._init(message, tokens, max_tokens)


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
