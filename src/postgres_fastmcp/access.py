"""Права одного запроса: серверный потолок, политика по claim токена и резолвер.

Потолок (``EffectiveAccess`` из ``DatabaseConfig``) задаёт максимум прав сервера.
Резолвер переводит токен запроса в эффективные права не выше потолка. Пока
auth-шаг не реализован, резолвер по умолчанию всегда возвращает потолок, а
``AccessPolicy(enforced=True)`` отклоняется, чтобы не выдать права молча.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastmcp.server.auth import AccessToken, AuthCheck, AuthContext
from pydantic import BaseModel, Field

from postgres_fastmcp.shared.enums import AccessMode


ERROR_POLICY_NOT_SUPPORTED = (
    "AccessPolicy.enforced=True is not supported yet: token claims do not narrow access in this version. "
    "Remove enforced=True or pass a custom access_resolver."
)


@dataclass(frozen=True, slots=True)
class EffectiveAccess:
    """Эффективные права одного запроса (или серверный потолок)."""

    access_mode: AccessMode
    write_mode: bool


class AccessPolicy(BaseModel):
    """Политика сужения прав по claim токена (правила применяются на auth-шаге)."""

    enforced: bool = False
    claim: str = "scope"
    write_values: list[str] = Field(default_factory=lambda: ["pg:write"])
    full_values: list[str] = Field(default_factory=lambda: ["pg:full"])


AccessResolver = Callable[[AccessToken | None], EffectiveAccess]


def build_resolver(ceiling: EffectiveAccess, policy: AccessPolicy) -> AccessResolver:
    """Резолвер по политике: сейчас всегда возвращает потолок.

    Args:
        ceiling: Серверный потолок прав.
        policy: Политика сужения по claim.

    Returns:
        Резолвер токен -> права.

    Raises:
        ValueError: Если policy.enforced=True (claim-правила ещё не реализованы).
    """
    if policy.enforced:
        raise ValueError(ERROR_POLICY_NOT_SUPPORTED)

    def resolve(_token: AccessToken | None) -> EffectiveAccess:
        return ceiling

    return resolve


def bounded_resolver(resolver: AccessResolver, ceiling: EffectiveAccess) -> AccessResolver:
    """Обернуть резолвер так, чтобы результат никогда не превышал потолок.

    Нужен для пользовательского ``access_resolver``: он может вернуть FULL или
    запись при серверном basic/read-only, а исполнитель строится по результату.
    """

    def resolve(token: AccessToken | None) -> EffectiveAccess:
        wanted = resolver(token)
        full = ceiling.access_mode == AccessMode.FULL and wanted.access_mode == AccessMode.FULL
        return EffectiveAccess(
            access_mode=AccessMode.FULL if full else AccessMode.BASIC,
            write_mode=ceiling.write_mode and wanted.write_mode,
        )

    return resolve


def full_access_check(resolver: AccessResolver) -> AuthCheck:
    """AuthCheck для full-тулов: тул виден и вызываем, только если эффективный режим FULL.

    Args:
        resolver: Резолвер токен -> права (токен None в stdio и без auth).

    Returns:
        Проверка для ``Tool.from_function(auth=...)``.
    """

    def check(ctx: AuthContext) -> bool:
        return resolver(ctx.token).access_mode == AccessMode.FULL

    return check
