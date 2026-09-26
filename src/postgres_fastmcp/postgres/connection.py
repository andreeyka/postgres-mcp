"""Пул подключений к базе данных (только жизненный цикл)."""

import asyncio
import logging

from psycopg_pool import AsyncConnectionPool

from postgres_fastmcp.shared.errors import ConnectionFailedError
from postgres_fastmcp.shared.utils import obfuscate_password


logger = logging.getLogger(__name__)


class DbConnPool:
    """Менеджер подключений к базе данных с использованием пула подключений psycopg."""

    def __init__(
        self,
        connection_url: str | None = None,
        min_size: int = 1,
        max_size: int = 5,
    ) -> None:
        """Инициализация пула подключений к базе данных.

        Args:
            connection_url: URL подключения к базе данных.
            min_size: Минимальное количество подключений в пуле.
            max_size: Максимальное количество подключений в пуле.
        """
        self.connection_url = connection_url
        self.min_size = min_size
        self.max_size = max_size
        self.pool: AsyncConnectionPool | None = None
        self._is_valid = False
        self._last_error: str | None = None
        # Сериализует открытие пула: без неё параллельные первые вызовы (asyncio.gather
        # в одном туле) создают несколько пулов и закрывают пул друг у друга.
        self._connect_lock = asyncio.Lock()

    async def pool_connect(self, connection_url: str | None = None) -> AsyncConnectionPool:
        """Инициализация пула подключений; возвращает существующий пул, если он уже действителен."""
        if self.pool and self._is_valid:
            return self.pool
        async with self._connect_lock:
            # Повторная проверка: пока ждали блокировку, пул мог открыть другой вызов.
            if self.pool and self._is_valid:
                return self.pool
            return await self._open_pool(connection_url)

    async def _open_pool(self, connection_url: str | None) -> AsyncConnectionPool:
        """Закрыть прежний пул и открыть новый; вызывается только под _connect_lock."""
        url = connection_url or self.connection_url
        self.connection_url = url
        if not url:
            self._is_valid = False
            self._last_error = "Database connection URL not provided"
            raise ValueError(self._last_error)

        await self.close()

        try:
            self.pool = AsyncConnectionPool(
                conninfo=url,
                min_size=self.min_size,
                max_size=self.max_size,
                open=False,
            )
            await self.pool.open()
            async with self.pool.connection() as conn, conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
            self._is_valid = True
            self._last_error = None
        except Exception as e:
            self._is_valid = False
            self._last_error = str(e)
            await self.close()
            obfuscated_error = obfuscate_password(str(e))
            raise ConnectionFailedError(obfuscated_error) from e
        else:
            return self.pool

    async def close(self) -> None:
        """Закрытие пула подключений."""
        if self.pool:
            try:
                await self.pool.close()
            except Exception as e:
                logger.warning("Error closing connection pool: %s", e)
            finally:
                self.pool = None
                self._is_valid = False

    @property
    def is_valid(self) -> bool:
        """Действителен ли пул подключений."""
        return self._is_valid

    @property
    def last_error(self) -> str | None:
        """Последнее сообщение об ошибке, если была."""
        return self._last_error

    def mark_invalid(self, error: str | None = None) -> None:
        """Пометить пул как недействительный (например, после сбоя подключения).

        Args:
            error: Необязательное сообщение об ошибке.
        """
        self._is_valid = False
        self._last_error = error
