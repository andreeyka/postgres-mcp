"""Пул подключений к базе данных (только жизненный цикл)."""

import asyncio
import logging
import weakref
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from postgres_fastmcp.shared.errors import ConnectionFailedError
from postgres_fastmcp.shared.utils import obfuscate_password


logger = logging.getLogger(__name__)

# psycopg_pool: через сколько секунд простоя закрывается соединение сверх min_size
DEFAULT_MAX_IDLE_SECONDS = 600.0

# Таймаут одного шага reset-callback (hypopg_reset(), DISCARD ALL): зависший сервер не должен
# блокировать воркер пула бесконечно. При истечении таймаута поднимается исключение, и пул
# выбрасывает такое соединение — так же, как при любой другой ошибке reset-callback.
RESET_TIMEOUT_SECONDS = 5.0


class DbConnPool:
    """Менеджер подключений к базе данных с использованием пула подключений psycopg."""

    def __init__(
        self,
        connection_url: str | None = None,
        min_size: int = 1,
        max_size: int = 5,
        max_idle: float = DEFAULT_MAX_IDLE_SECONDS,
    ) -> None:
        """Инициализация пула подключений к базе данных.

        Args:
            connection_url: URL подключения к базе данных.
            min_size: Минимальное количество подключений в пуле.
            max_size: Максимальное количество подключений в пуле.
            max_idle: Через сколько секунд простоя пул закрывает соединение сверх min_size.
        """
        self.connection_url = connection_url
        self.min_size = min_size
        self.max_size = max_size
        self.max_idle = max_idle
        # Соединения, на которых создавались гипотетические индексы hypopg: индексы живут в памяти сессии
        # и переживают ROLLBACK, поэтому сбрасываются при возврате соединения в пул (reset-callback).
        self._hypopg_connections: weakref.WeakSet[AsyncConnection[Any]] = weakref.WeakSet()
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
                max_idle=self.max_idle,
                reset=self._reset_connection,
                # DISCARD ALL в reset-callback включает DEALLOCATE ALL: он снимает на сервере
                # подготовленные запросы. Автоподготовка psycopg (prepare_threshold=5 по умолчанию)
                # держит свой клиентский кэш и после DEALLOCATE ALL ссылалась бы на запросы,
                # которых на сервере уже нет. Отключаем автоподготовку на уровне соединений пула.
                kwargs={"prepare_threshold": None},
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
        except BaseException:
            # Отмена (клиентский таймаут) посреди open() или SELECT 1: без закрытия пул остался бы
            # невалидным и продолжал бы подключаться в фоне.
            await self.close()
            raise
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

    def mark_hypopg_used(self, connection: AsyncConnection[Any]) -> None:
        """Пометить соединение: при возврате в пул на нём выполнится hypopg_reset().

        Args:
            connection: Соединение пула, на котором выполнялся hypopg_create_index.
        """
        self._hypopg_connections.add(connection)

    async def _reset_connection(self, connection: AsyncConnection[Any]) -> None:
        """reset-callback пула: соединение возвращается в пул без состояния сессии.

        Роль (SET ROLE), параметры (SET), временные таблицы, курсоры WITH HOLD, LISTEN,
        advisory-блокировки, подготовленные запросы (PREPARE) — всё это иначе пережило бы
        возврат соединения и было бы видно следующему запросу, full или basic, на том же
        соединении. Сначала соединение переводится в autocommit (пул требует вернуть его в
        IDLE) — это отдельный шаг со своей нейтральной ошибкой, ведь он ничего не говорит ни о
        hypopg, ни о DISCARD ALL. Для помеченного соединения затем сбрасываются гипотетические
        индексы hypopg (они живут в памяти сессии и переживают ROLLBACK), затем в любом случае —
        DISCARD ALL; у каждого из этих запросов — таймаут RESET_TIMEOUT_SECONDS, зависший сервер
        не должен блокировать воркер пула. Ошибка любого из шагов (включая таймаут) поднимается
        дальше: psycopg_pool закрывает такое соединение, состояние сессии на нём неизвестно. Это
        касается и UndefinedFunction: basic создаёт индексы под SET LOCAL search_path = public,
        а сброс идёт вне этой транзакции, на search_path роли по умолчанию. Если hypopg лежит в
        public, а public нет в пути роли, индексы созданы, но hypopg_reset() не найден —
        оставить такое соединение в пуле значило бы показать чужие гипотетические индексы
        следующим вызовам.
        """
        if connection.closed:
            # psycopg_pool вызывает reset и для соединения, которое сам же закрыл при возврате
            # (ACTIVE/сбойное): выполнять запрос уже некуда, а execute на закрытом соединении
            # упал бы с вводящим в заблуждение предупреждением "Failed to reset...".
            return
        is_hypopg = connection in self._hypopg_connections
        self._hypopg_connections.discard(connection)
        try:
            if not connection.autocommit:
                await connection.set_autocommit(True)
        except Exception as e:
            logger.warning("Failed to prepare a returned connection for reset: %s", e)
            raise
        try:
            if is_hypopg:
                async with asyncio.timeout(RESET_TIMEOUT_SECONDS):
                    await connection.execute("SELECT hypopg_reset()")
        except Exception as e:
            logger.warning("Failed to reset hypothetical indexes on a returned connection: %s", e)
            raise
        try:
            async with asyncio.timeout(RESET_TIMEOUT_SECONDS):
                await connection.execute("DISCARD ALL")
        except Exception as e:
            logger.warning("Failed to discard session state on a returned connection: %s", e)
            raise


# libpq считает connect_timeout в целых секундах, минимум 2; внешний таймаут /health — у вызывающего
CHECK_CONNECT_TIMEOUT_SECONDS = 2


async def check_connection(connection_url: str) -> None:
    """Открыть отдельное соединение, выполнить SELECT 1 и закрыть его (проверка /health).

    Пул не трогается: проба не открывает пул на сервере, к которому ещё не было запросов, и
    отдаёт настоящую ошибку psycopg сразу, а не PoolTimeout через 30 секунд. Текст ошибки может
    содержать строку подключения — маскирует вызывающий.

    Соединение закрывается при любом выходе, включая отмену по внешнему таймауту во время
    SELECT 1 (``async with``). Отмена посреди connect() закрывать нечего: незавершённый PGconn
    остаётся только внутри генератора psycopg и освобождается (PQfinish) вместе с ним.
    """
    async with await AsyncConnection.connect(
        connection_url, autocommit=True, connect_timeout=CHECK_CONNECT_TIMEOUT_SECONDS
    ) as conn:
        await conn.execute("SELECT 1")
