"""Проверка прав роли БД при старте basic: basic — защита в глубину, граница доступа — права роли.

Запросы идут через канал сервера (catalog_driver), находки — английские фразы для одной строки WARNING.
"""

from dataclasses import dataclass

from psycopg import (
    Error as PsycopgError,
    OperationalError,
)
from psycopg.errors import InsufficientPrivilege

from postgres_fastmcp.postgres.catalog import (
    QUERY_ROLE_ATTRIBUTES,
    QUERY_ROLE_FOREIGN_SCHEMAS,
    QUERY_ROLE_PREDEFINED_MEMBERSHIPS,
    QUERY_ROLE_UNPREFIXED_TABLES,
)
from postgres_fastmcp.postgres.ports import QueryExecutorPort
from postgres_fastmcp.shared.errors import (
    ConnectionFailedError,
    ConnectionNotEstablishedError,
    QueryCancelledError,
    QueryTimeoutError,
)
from postgres_fastmcp.shared.logger import get_logger
from postgres_fastmcp.shared.utils import obfuscate_password


logger = get_logger(__name__)

# Длинный список схем в одной строке лога не читается: первые MAX_LISTED_SCHEMAS и многоточие.
MAX_LISTED_SCHEMAS = 10

# БД недоступна, запрос отменён или не успел: проверка пропускается тихо, сервер работает дальше.
# OperationalError — база и для сбоя подключения, и для psycopg_pool.PoolTimeout (его подкласс).
_UNREACHABLE_ERRORS = (
    OperationalError,
    ConnectionFailedError,
    ConnectionNotEstablishedError,
    QueryTimeoutError,
    QueryCancelledError,
    TimeoutError,
)


@dataclass(frozen=True, slots=True)
class RoleFindings:
    """Роль подключения и её права сверх basic (английские фразы для лога)."""

    role: str
    findings: list[str]


async def basic_role_findings(catalog: QueryExecutorPort, table_prefix: str | None) -> RoleFindings:
    """Права роли подключения, которых basic не ожидает: всё, что шире таблиц public (с префиксом).

    Args:
        catalog: Исполнитель шаблонов каталога (канал сервера).
        table_prefix: Префикс таблиц basic; без него таблицы public без префикса не проверяются.

    Returns:
        Имя роли и находки; пустой список — роль ограничена public.
    """
    rows = await catalog.execute(QUERY_ROLE_ATTRIBUTES) or []
    if not rows:
        return RoleFindings(role="", findings=[])
    attributes = rows[0].cells
    role = str(attributes["role_name"])
    # Суперпользователю pg_has_role и has_*_privilege всегда отвечают true: остальное подразумевается.
    if attributes["rolsuper"]:
        return RoleFindings(role=role, findings=["superuser"])
    findings = []
    if attributes["rolbypassrls"]:
        findings.append("BYPASSRLS")
    # CREATEROLE на PG <= 15 позволяет роли выдать себе членство в предопределённых ролях самостоятельно
    # (WITH ADMIN OPTION не нужен); с PG 16 для этого дополнительно нужно явное членство в целевой роли.
    if attributes["rolcreaterole"]:
        findings.append("CREATEROLE")
    findings += await _membership_findings(catalog)
    findings += await _schema_findings(catalog)
    if table_prefix:
        findings += await _unprefixed_table_findings(catalog, table_prefix)
    return RoleFindings(role=role, findings=findings)


async def _membership_findings(catalog: QueryExecutorPort) -> list[str]:
    """Членство в предопределённых ролях, открывающих данные или сервер целиком."""
    rows = await catalog.execute(QUERY_ROLE_PREDEFINED_MEMBERSHIPS) or []
    names = [str(row.cells["rolname"]) for row in rows]
    return [f"member of {', '.join(names)}"] if names else []


async def _schema_findings(catalog: QueryExecutorPort) -> list[str]:
    """USAGE на схемы, кроме public, information_schema и pg_*."""
    rows = await catalog.execute(QUERY_ROLE_FOREIGN_SCHEMAS) or []
    names = [str(row.cells["nspname"]) for row in rows]
    if not names:
        return []
    listed = ", ".join(names[:MAX_LISTED_SCHEMAS])
    if len(names) > MAX_LISTED_SCHEMAS:
        listed += ", …"
    return [f"USAGE on schemas: {listed}"]


async def _unprefixed_table_findings(catalog: QueryExecutorPort, table_prefix: str) -> list[str]:
    """SELECT на отношения public без префикса: basic их отклоняет, но роль может их читать."""
    rows = await catalog.execute(QUERY_ROLE_UNPREFIXED_TABLES, [table_prefix]) or []
    count = int(rows[0].cells["unprefixed"]) if rows else 0
    if not count:
        return []
    noun = "table" if count == 1 else "tables"
    return [f"SELECT on {count} public {noun} without prefix '{table_prefix}'"]


async def warn_about_basic_role(catalog: QueryExecutorPort, table_prefix: str | None) -> None:
    """Одна строка WARNING, если роль может больше, чем basic.

    БД недоступна (подключение, таймаут, отмена) — одна строка INFO, сервер стартует дальше:
    это ожидаемо на старте и не повод шуметь. Роли закрыто чтение каталога (например,
    REVOKE SELECT ON pg_roles FROM PUBLIC) — тоже INFO: проверять нечего, и выдавать роли
    доступ к каталогу ради этой проверки не нужно. Любая другая ошибка Postgres при выполнении
    шаблонов каталога (например, баг в одном из них) — одна строка WARNING «failed», а не
    тихий пропуск: иначе реальная дыра в проверке выглядела бы как штатный пропуск.
    ValueError/TypeError CatalogSqlExecutor — ошибка кода, а не БД, — не перехватываются здесь
    и доходят до вызывающего.

    Args:
        catalog: Исполнитель шаблонов каталога (канал сервера).
        table_prefix: Префикс таблиц basic из конфигурации.
    """
    try:
        result = await basic_role_findings(catalog, table_prefix)
    except _UNREACHABLE_ERRORS as e:
        logger.info("Basic role check skipped: %s", obfuscate_password(str(e) or type(e).__name__))
    except InsufficientPrivilege as e:
        logger.info("Basic role check skipped: the role cannot read the catalog (%s)", str(e).strip())
    except PsycopgError as e:
        logger.warning("Basic role check failed: %s", obfuscate_password(str(e) or type(e).__name__))
    else:
        if result.findings:
            logger.warning(
                "Database role '%s' has privileges beyond basic mode: %s. In basic mode the SQL validator is then "
                "the only barrier; grant the role access to 'public' only (see README).",
                result.role,
                "; ".join(result.findings),
            )
