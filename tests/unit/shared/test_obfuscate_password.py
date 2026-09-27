"""obfuscate_password: маскируется только сегмент пароля, остальной текст не трогается."""

import pytest

from postgres_fastmcp.shared.utils import obfuscate_password


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Пароль совпадает с пользователем: маска на месте пользователя выдала бы пароль
        ("postgresql://p4ss:p4ss@h/d", "postgresql://p4ss:****@h/d"),
        # Пароль совпадает с частью хоста
        ("postgresql://u:db@db.local:5432/d", "postgresql://u:****@db.local:5432/d"),
        ("postgresql://u:secret@h:5432/d?sslmode=require", "postgresql://u:****@h:5432/d?sslmode=require"),
        ("postgresql://u:p%40ss@[::1]:5432/d", "postgresql://u:****@[::1]:5432/d"),
        ("postgresql://u@h/d", "postgresql://u@h/d"),
        ("could not connect: password=secret host=h", "could not connect: password=**** host=h"),
        ("host=h password='se cret' user=u", "host=h password='****' user=u"),
        ('host=h password="se cret" user=u', 'host=h password="****" user=u'),
        ("error at postgresql://u:secret@h/d; retry", "error at postgresql://u:****@h/d; retry"),
        ("", ""),
        (None, None),
    ],
)
def test_obfuscate_password(text: str | None, expected: str | None) -> None:
    assert obfuscate_password(text) == expected
