"""Тесты публичного API пакета postgres_fastmcp."""


def test_public_api_exports() -> None:
    import postgres_fastmcp as pkg

    expected = {
        "create_server",
        "Settings",
        "LocalProvider",
        "FileSystemProvider",
        "Middleware",
        "__version__",
    }
    missing = {name for name in expected if not hasattr(pkg, name)}
    assert not missing, f"Missing exports: {missing}"


def test_create_server_is_callable() -> None:
    import postgres_fastmcp as pkg

    assert callable(pkg.create_server)


def test_settings_is_class() -> None:
    import postgres_fastmcp as pkg

    assert isinstance(pkg.Settings, type)
