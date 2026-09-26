"""Тесты access.py: потолок прав, политика, резолвер и проверка full-тулов."""

import dataclasses
from unittest.mock import MagicMock

import pytest
from fastmcp.server.auth import AccessToken, AuthContext
from pydantic import ValidationError

from postgres_fastmcp.access import (
    AccessPolicy,
    EffectiveAccess,
    bounded_resolver,
    build_resolver,
    full_access_check,
    resolve_access,
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


@pytest.mark.parametrize("field", ["write_values", "full_values"])
@pytest.mark.parametrize("value", ["", "  "])
def test_access_policy_rejects_empty_or_blank_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        AccessPolicy(**{field: [value]})


def test_access_policy_defaults_construct_despite_the_non_empty_constraint() -> None:
    policy = AccessPolicy()
    assert policy.write_values == ["pg:write"]
    assert policy.full_values == ["pg:full"]


@pytest.mark.parametrize("ceiling", _ALL_ACCESS)
@pytest.mark.parametrize("token", [None, _token([]), _token(["pg:write", "pg:full"])])
def test_default_resolver_returns_ceiling(ceiling: EffectiveAccess, token: AccessToken | None) -> None:
    """Без auth-шага сужения нет: любой токен (или его отсутствие) даёт серверный потолок."""
    assert build_resolver(ceiling, AccessPolicy())(token) == ceiling


_FULL_WRITE = EffectiveAccess(AccessMode.FULL, write_mode=True)
_READ_ONLY = EffectiveAccess(AccessMode.BASIC, write_mode=False)
_ENFORCED = AccessPolicy(enforced=True)


def _claims_token(claims: dict[str, object], scopes: list[str] | None = None) -> AccessToken:
    return AccessToken(token="t", client_id="c", scopes=scopes or [], claims=claims)


@pytest.mark.parametrize(
    ("scopes", "expected"),
    [
        ([], EffectiveAccess(AccessMode.BASIC, write_mode=False)),
        (["pg:write"], EffectiveAccess(AccessMode.BASIC, write_mode=True)),
        (["pg:full"], EffectiveAccess(AccessMode.FULL, write_mode=False)),
        (["pg:full", "pg:write", "other"], EffectiveAccess(AccessMode.FULL, write_mode=True)),
    ],
)
def test_scope_claim_reads_token_scopes(scopes: list[str], expected: EffectiveAccess) -> None:
    """claim='scope' берётся из token.scopes, а не из claims."""
    token = _claims_token({"scope": "pg:full pg:write"}, scopes=scopes)
    assert resolve_access(_FULL_WRITE, token, _ENFORCED) == expected


@pytest.mark.parametrize(
    ("groups", "expected"),
    [
        (["analyst"], EffectiveAccess(AccessMode.BASIC, write_mode=False)),
        (["backend-writers"], EffectiveAccess(AccessMode.BASIC, write_mode=True)),
        (["dba"], EffectiveAccess(AccessMode.FULL, write_mode=True)),
        ("dba analyst", EffectiveAccess(AccessMode.FULL, write_mode=True)),
    ],
)
def test_groups_claim_accepts_list_and_space_separated_string(groups: object, expected: EffectiveAccess) -> None:
    policy = AccessPolicy(enforced=True, claim="groups", write_values=["dba", "backend-writers"], full_values=["dba"])
    assert resolve_access(_FULL_WRITE, _claims_token({"groups": groups}), policy) == expected


def test_nested_claim_path() -> None:
    policy = AccessPolicy(enforced=True, claim="realm_access.roles", write_values=["writer"], full_values=["admin"])
    token = _claims_token({"realm_access": {"roles": ["admin", "writer"]}})
    assert resolve_access(_FULL_WRITE, token, policy) == _FULL_WRITE


def test_claim_name_with_dots_is_looked_up_as_a_whole_key_first() -> None:
    """Auth0 кладёт роли в claim с URL-именем: ключ с точками целиком важнее пути."""
    policy = AccessPolicy(enforced=True, claim="https://example.com/roles", write_values=["w"], full_values=["f"])
    token = _claims_token({"https://example.com/roles": ["f", "w"]})
    assert resolve_access(_FULL_WRITE, token, policy) == _FULL_WRITE


def test_literal_dotted_key_with_none_value_falls_back_to_the_nested_path() -> None:
    """Ключ целиком совпал с путём claim, но его значение None: путь через точку берёт верх."""
    policy = AccessPolicy(enforced=True, claim="realm_access.roles", full_values=["admin"])
    token = _claims_token({"realm_access.roles": None, "realm_access": {"roles": ["admin"]}})
    assert resolve_access(_FULL_WRITE, token, policy) == EffectiveAccess(AccessMode.FULL, write_mode=False)


def test_string_claim_value_with_surrounding_whitespace_matches() -> None:
    policy = AccessPolicy(enforced=True, claim="groups", full_values=["dba"])
    token = _claims_token({"groups": " dba "})
    assert resolve_access(_FULL_WRITE, token, policy) == EffectiveAccess(AccessMode.FULL, write_mode=False)


def test_scope_matching_is_case_sensitive() -> None:
    """scopes=['PG:FULL'] не совпадает с дефолтным full_values=['pg:full']: регистр важен."""
    token = _token(["PG:FULL"])
    assert resolve_access(_FULL_WRITE, token, _ENFORCED) == _READ_ONLY


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"groups": None},
        {"groups": 42},
        {"groups": {"dba": True}},
        {"groups": [42, {"x": 1}]},
        {"realm_access": "dba"},
    ],
)
def test_missing_path_or_other_type_gives_no_values(claims: dict[str, object]) -> None:
    for claim in ("groups", "realm_access.roles"):
        policy = AccessPolicy(enforced=True, claim=claim, write_values=["dba"], full_values=["dba"])
        assert resolve_access(_FULL_WRITE, _claims_token(claims), policy) == _READ_ONLY


def test_token_claims_none_gives_no_values() -> None:
    """SDK AccessToken допускает claims=None."""
    token = AccessToken(token="t", client_id="c", scopes=[])
    token.claims = None
    policy = AccessPolicy(enforced=True, claim="groups", full_values=["dba"])
    assert resolve_access(_FULL_WRITE, token, policy) == _READ_ONLY


@pytest.mark.parametrize("ceiling", _ALL_ACCESS)
def test_token_never_exceeds_ceiling(ceiling: EffectiveAccess) -> None:
    token = _token(["pg:full", "pg:write"])
    assert resolve_access(ceiling, token, _ENFORCED) == ceiling


@pytest.mark.parametrize("ceiling", _ALL_ACCESS)
def test_no_token_or_policy_off_gives_ceiling(ceiling: EffectiveAccess) -> None:
    assert resolve_access(ceiling, None, _ENFORCED) == ceiling
    assert resolve_access(ceiling, _token([]), AccessPolicy()) == ceiling


def test_build_resolver_applies_the_policy() -> None:
    resolver = build_resolver(_FULL_WRITE, _ENFORCED)
    assert resolver(_token(["pg:write"])) == EffectiveAccess(AccessMode.BASIC, write_mode=True)
    assert resolver(None) == _FULL_WRITE


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


@pytest.mark.parametrize(("scopes", "allowed"), [([], False), (["pg:write"], False), (["pg:full"], True)])
def test_full_access_check_reads_the_token_claim(scopes: list[str], *, allowed: bool) -> None:
    check = full_access_check(build_resolver(_FULL_WRITE, _ENFORCED))
    assert check(AuthContext(token=_token(scopes), component=MagicMock())) is allowed
