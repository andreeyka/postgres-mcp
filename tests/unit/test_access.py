"""Тесты access.py: потолок прав, политика, резолвер и проверка full-тулов."""

import dataclasses
from unittest.mock import MagicMock

import pytest
from fastmcp.server.auth import AccessToken, AuthContext

from postgres_fastmcp.access import (
    AccessPolicy,
    EffectiveAccess,
    bounded_resolver,
    build_resolver,
    full_access_check,
)
from postgres_fastmcp.shared.enums import AccessMode


_ALL_ACCESS = [
    EffectiveAccess(AccessMode.BASIC, write_mode=False),
    EffectiveAccess(AccessMode.BASIC, write_mode=True),
    EffectiveAccess(AccessMode.FULL, write_mode=False),
    EffectiveAccess(AccessMode.FULL, write_mode=True),
]


def _token(scopes: list[str]) -> AccessToken:
    return AccessToken(token="t", client_id="c", scopes=scopes)


def test_effective_access_is_frozen_and_hashable() -> None:
    access = EffectiveAccess(AccessMode.FULL, write_mode=True)
    assert {access: 1}[EffectiveAccess(AccessMode.FULL, write_mode=True)] == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        access.write_mode = False  # type: ignore[misc]


def test_access_policy_defaults() -> None:
    policy = AccessPolicy()
    assert policy.enforced is False
    assert policy.claim == "scope"
    assert policy.write_values == ["pg:write"]
    assert policy.full_values == ["pg:full"]


@pytest.mark.parametrize("ceiling", _ALL_ACCESS)
@pytest.mark.parametrize("token", [None, _token([]), _token(["pg:write", "pg:full"])])
def test_default_resolver_returns_ceiling(ceiling: EffectiveAccess, token: AccessToken | None) -> None:
    """Без auth-шага сужения нет: любой токен (или его отсутствие) даёт серверный потолок."""
    assert build_resolver(ceiling, AccessPolicy())(token) == ceiling


def test_enforced_policy_is_rejected_until_claims_are_supported() -> None:
    """enforced=True без реализации claim-правил молча дал бы полный потолок: отказываем явно."""
    ceiling = EffectiveAccess(AccessMode.FULL, write_mode=True)
    with pytest.raises(ValueError, match="AccessPolicy.enforced"):
        build_resolver(ceiling, AccessPolicy(enforced=True))


@pytest.mark.parametrize("ceiling", _ALL_ACCESS)
@pytest.mark.parametrize("wanted", _ALL_ACCESS)
def test_bounded_resolver_never_exceeds_ceiling(ceiling: EffectiveAccess, wanted: EffectiveAccess) -> None:
    resolved = bounded_resolver(lambda _token: wanted, ceiling)(None)
    full = ceiling.access_mode == AccessMode.FULL and wanted.access_mode == AccessMode.FULL
    assert resolved.access_mode == (AccessMode.FULL if full else AccessMode.BASIC)
    assert resolved.write_mode == (ceiling.write_mode and wanted.write_mode)


def test_bounded_resolver_passes_token_through() -> None:
    seen: list[AccessToken | None] = []
    ceiling = EffectiveAccess(AccessMode.FULL, write_mode=False)
    token = _token(["x"])

    def resolver(t: AccessToken | None) -> EffectiveAccess:
        seen.append(t)
        return ceiling

    bounded_resolver(resolver, ceiling)(token)
    assert seen == [token]


@pytest.mark.parametrize(
    ("ceiling", "allowed"),
    [
        (EffectiveAccess(AccessMode.BASIC, write_mode=True), False),
        (EffectiveAccess(AccessMode.FULL, write_mode=False), True),
    ],
)
def test_full_access_check_follows_resolver(ceiling: EffectiveAccess, *, allowed: bool) -> None:
    """Проверка full-тула пропускает, только если эффективный режим FULL; токен None — потолок."""
    check = full_access_check(build_resolver(ceiling, AccessPolicy()))
    assert check(AuthContext(token=None, component=MagicMock())) is allowed
