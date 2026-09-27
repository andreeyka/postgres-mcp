"""Права одного запроса: серверный потолок, политика по claim токена и резолвер.

Потолок (``EffectiveAccess`` из ``DatabaseConfig``) задаёт максимум прав сервера.
Резолвер переводит токен запроса в эффективные права не выше потолка: при
``AccessPolicy(enforced=True)`` — по значениям claim токена (``resolve_access``),
иначе всегда потолок.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastmcp.server.auth import AccessToken, AuthCheck, AuthContext
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from postgres_fastmcp.shared.enums import AccessMode


_NonBlankStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


@dataclass(frozen=True, slots=True)
class EffectiveAccess:
    """Эффективные права одного запроса (или серверный потолок)."""

    access_mode: AccessMode
    write_mode: bool


class AccessPolicy(BaseModel):
    """Политика сужения прав по claim токена.

    ``claim`` — имя claim или путь через точку (``realm_access.roles``); ``scope`` читается
    из ``AccessToken.scopes``. Запись даёт любое значение из ``write_values``, режим FULL —
    любое значение из ``full_values``; оба никогда не выше серверного потолка. Значения в
    ``write_values``/``full_values`` не могут быть пустыми или состоять только из пробелов.
    Неизвестный ключ (опечатка ``enforcd`` в config.json или
    ``MCP_AUTH_ACCESS_POLICY__ENFORCD``) — ошибка, а не молчаливое ``enforced=False``.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    enforced: bool = False
    claim: str = "scope"
    write_values: list[_NonBlankStr] = Field(default_factory=lambda: ["pg:write"])
    full_values: list[_NonBlankStr] = Field(default_factory=lambda: ["pg:full"])


AccessResolver = Callable[[AccessToken | None], EffectiveAccess]


def _claim_values(token: AccessToken, claim: str) -> set[str]:
    """Значения claim токена как множество строк.

    ``scope`` берётся из ``token.scopes`` (верификатор FastMCP уже разобрал ``scope``/``scp``).
    Иначе сначала ищется ключ целиком (claim с точкой в имени, например URL-namespace Auth0),
    затем путь через точку по вложенным словарям. Строка делится по пробелам, из списка
    берутся строковые элементы; отсутствие пути и другие типы дают пустое множество.
    """
    if claim == "scope":
        return set(token.scopes)
    claims: dict[str, Any] = token.claims or {}
    value: Any = claims.get(claim)
    if value is None:
        value = claims
        for part in claim.split("."):
            if not isinstance(value, dict):
                return set()
            value = value.get(part)
    if isinstance(value, str):
        return set(value.split())
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


def resolve_access(ceiling: EffectiveAccess, token: AccessToken | None, policy: AccessPolicy) -> EffectiveAccess:
    """Эффективные права запроса по claim токена, никогда не выше потолка.

    Args:
        ceiling: Серверный потолок прав.
        token: Токен запроса; None в stdio и без auth.
        policy: Политика сужения по claim.

    Returns:
        Потолок, если политика не включена или токена нет; иначе права по значениям claim.
    """
    if token is None or not policy.enforced:
        return ceiling
    values = _claim_values(token, policy.claim)
    full = ceiling.access_mode == AccessMode.FULL and bool(values & set(policy.full_values))
    return EffectiveAccess(
        access_mode=AccessMode.FULL if full else AccessMode.BASIC,
        write_mode=ceiling.write_mode and bool(values & set(policy.write_values)),
    )


def build_resolver(ceiling: EffectiveAccess, policy: AccessPolicy) -> AccessResolver:
    """Резолвер токен -> права по политике (``resolve_access`` с зафиксированными потолком и политикой).

    Args:
        ceiling: Серверный потолок прав.
        policy: Политика сужения по claim.

    Returns:
        Резолвер токен -> права.
    """

    def resolve(token: AccessToken | None) -> EffectiveAccess:
        return resolve_access(ceiling, token, policy)

    return resolve


def clamp_to_ceiling(wanted: EffectiveAccess, ceiling: EffectiveAccess) -> EffectiveAccess:
    """Покомпонентный минимум прав: результат никогда не превышает потолок.

    FULL — только если FULL и в потолке, и в запросе; запись — только если
    ``write_mode is True`` в обоих (небулево «истинное» значение записью не считается).
    """
    full = ceiling.access_mode == AccessMode.FULL and wanted.access_mode == AccessMode.FULL
    return EffectiveAccess(
        access_mode=AccessMode.FULL if full else AccessMode.BASIC,
        write_mode=ceiling.write_mode is True and wanted.write_mode is True,
    )


def bounded_resolver(resolver: AccessResolver, ceiling: EffectiveAccess) -> AccessResolver:
    """Обернуть резолвер так, чтобы результат никогда не превышал потолок.

    Нужен для пользовательского ``access_resolver``: он может вернуть FULL или
    запись при серверном basic/read-only, а исполнитель строится по результату.
    """

    def resolve(token: AccessToken | None) -> EffectiveAccess:
        return clamp_to_ceiling(resolver(token), ceiling)

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
