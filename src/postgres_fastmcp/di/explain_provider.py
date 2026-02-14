"""Провайдеры ExplainService (plain / analyze / hypothetical)."""

from fastmcp.dependencies import Depends

from postgres_fastmcp.di.db_access_provider import DbAccessServiceProvider
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.explain.service import ExplainService


def get_explain_plain_service(db: DbAccessService = DbAccessServiceProvider) -> ExplainService:
    """Получить сервис explain (обычный EXPLAIN)."""
    return ExplainService(db, mode="plain")


def get_explain_analyze_service(db: DbAccessService = DbAccessServiceProvider) -> ExplainService:
    """Получить сервис explain (EXPLAIN ANALYZE)."""
    return ExplainService(db, mode="analyze")


def get_explain_hypothetical_service(
    db: DbAccessService = DbAccessServiceProvider,
) -> ExplainService:
    """Получить сервис explain с поддержкой гипотетических индексов."""
    return ExplainService(db, mode="hypothetical")


ExplainPlainServiceProvider = Depends(get_explain_plain_service)
ExplainAnalyzeServiceProvider = Depends(get_explain_analyze_service)
ExplainHypotheticalServiceProvider = Depends(get_explain_hypothetical_service)
