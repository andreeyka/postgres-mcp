"""Проверка состояния репликации: реплика/праймари, лаг, активность, слоты."""

from dataclasses import dataclass

from postgres_fastmcp.domains.health.base import BaseHealthCalc
from postgres_fastmcp.postgres.ports import QueryExecutorPort


@dataclass
class ReplicationSlot:
    """Информация о репликационном слоте.

    Attributes:
        slot_name: Имя репликационного слота.
        database: Имя базы данных для слота.
        active: Активен ли слот в данный момент.
    """

    slot_name: str
    database: str
    active: bool


@dataclass
class ReplicationMetrics:
    """Метрики для проверки состояния репликации базы данных.

    Attributes:
        is_replica: Является ли эта база данных репликой.
        replication_lag_seconds: Задержка репликации в секундах, None если недоступно.
        is_replicating: Активна ли репликация (реплика получает WAL / у праймари есть standby).
        replication_slots: Список репликационных слотов.
    """

    is_replica: bool
    replication_lag_seconds: float | None
    is_replicating: bool
    replication_slots: list[ReplicationSlot]


class ReplicationCalc(BaseHealthCalc):
    """Калькулятор для проверок состояния репликации базы данных."""

    # Константы версий PostgreSQL (формат: major*10000 + minor*100 + patch)
    MIN_VERSION_REPLICATION_SLOTS = 90400  # PostgreSQL 9.4.0
    MIN_VERSION_WAL_FUNCTIONS = 100000  # PostgreSQL 10.0.0

    def __init__(self, sql_driver: QueryExecutorPort) -> None:
        super().__init__(sql_driver)
        self._server_version: int | None = None
        self._feature_support: dict[str, bool] = {}

    async def replication_health_check(self) -> str:
        """Проверка состояния репликации, включая задержку и слоты.

        Returns:
            Строка с описанием состояния репликации.
        """
        metrics = await self._get_replication_metrics()
        result = []

        if metrics.is_replica:
            result.append("This is a replica database.")
            if not metrics.is_replicating:
                result.append("WARNING: Replica is not actively replicating from primary!")
            else:
                result.append("Replica is actively replicating from primary.")

            if metrics.replication_lag_seconds is not None:
                if metrics.replication_lag_seconds == 0:
                    result.append("No replication lag detected.")
                else:
                    result.append(f"Replication lag: {metrics.replication_lag_seconds:.1f} seconds")
        else:
            result.append("This is a primary database.")
            if metrics.is_replicating:
                result.append("Has active replicas connected.")
            else:
                result.append("No active replicas connected.")

        if metrics.replication_slots:
            active_slots = [s for s in metrics.replication_slots if s.active]
            inactive_slots = [s for s in metrics.replication_slots if not s.active]

            if active_slots:
                result.append("\nActive replication slots:")
                result.extend(f"- {slot.slot_name} (database: {slot.database})" for slot in active_slots)

            if inactive_slots:
                result.append("\nInactive replication slots:")
                result.extend(f"- {slot.slot_name} (database: {slot.database})" for slot in inactive_slots)
        else:
            result.append("\nNo replication slots found.")

        return "\n".join(result)

    async def _get_replication_metrics(self) -> ReplicationMetrics:
        """Получение комплексной метрики репликации.

        Смысл ``is_replicating`` зависит от роли узла: для реплики это «получаем ли WAL
        от праймари» (``pg_stat_wal_receiver``), для праймари — «есть ли подключённые
        standby» (``pg_stat_replication``). Раньше и там и там использовался
        ``pg_stat_replication``, из-за чего здоровая реплика ложно помечалась как
        «не реплицирующая».
        """
        is_replica = await self._is_replica()
        is_replicating = await self._is_receiving_wal() if is_replica else await self._is_replicating()
        return ReplicationMetrics(
            is_replica=is_replica,
            replication_lag_seconds=await self._get_replication_lag(),
            is_replicating=is_replicating,
            replication_slots=await self._get_replication_slots(),
        )

    async def _is_replica(self) -> bool:
        """True если база данных в режиме восстановления (реплика)."""
        rows = await self._rows("SELECT pg_is_in_recovery()")
        return bool(rows[0]["pg_is_in_recovery"]) if rows else False

    async def _get_replication_lag(self) -> float | None:
        """Получение задержки репликации в секундах (None если недоступна)."""
        if not self._feature_supported("replication_lag"):
            return None

        version = await self._get_server_version()
        if version >= self.MIN_VERSION_WAL_FUNCTIONS:
            query = """
                SELECT
                    CASE
                        WHEN NOT pg_is_in_recovery() OR pg_last_wal_receive_lsn() = pg_last_wal_replay_lsn() THEN 0
                        ELSE EXTRACT (EPOCH FROM NOW() - pg_last_xact_replay_timestamp())
                    END
                AS replication_lag
            """
        else:
            query = """
                SELECT
                    CASE
                        WHEN NOT pg_is_in_recovery()
                            OR pg_last_xlog_receive_location() = pg_last_xlog_replay_location()
                        THEN 0
                        ELSE EXTRACT (EPOCH FROM NOW() - pg_last_xact_replay_timestamp())
                    END
                AS replication_lag
            """

        try:
            rows = await self._rows(query)
            return float(rows[0]["replication_lag"]) if rows else None
        except Exception:
            self._feature_support["replication_lag"] = False
            return None

    async def _get_replication_slots(self) -> list[ReplicationSlot]:
        """Получение информации о репликационных слотах."""
        if await self._get_server_version() < self.MIN_VERSION_REPLICATION_SLOTS or not self._feature_supported(
            "replication_slots"
        ):
            return []

        try:
            rows = await self._rows("SELECT slot_name, database, active FROM pg_replication_slots")
            return [
                ReplicationSlot(
                    slot_name=row["slot_name"],
                    database=row["database"],
                    active=row["active"],
                )
                for row in rows
            ]
        except Exception:
            self._feature_support["replication_slots"] = False
            return []

    async def _is_replicating(self) -> bool:
        """True если у праймари есть подключённые standby (по pg_stat_replication)."""
        if not self._feature_supported("replicating"):
            return False

        try:
            rows = await self._rows("SELECT state FROM pg_stat_replication")
            return bool(rows)
        except Exception:
            self._feature_support["replicating"] = False
            return False

    async def _is_receiving_wal(self) -> bool:
        """True если реплика получает WAL от праймари (по pg_stat_wal_receiver, PG 9.6+)."""
        if not self._feature_supported("wal_receiver"):
            return False

        try:
            rows = await self._rows("SELECT status FROM pg_stat_wal_receiver")
            return bool(rows)
        except Exception:
            self._feature_support["wal_receiver"] = False
            return False

    async def _get_server_version(self) -> int:
        """Получение версии сервера PostgreSQL в виде числа (например, 100000 для 10.0)."""
        if self._server_version is None:
            rows = await self._rows("SHOW server_version_num")
            self._server_version = int(rows[0]["server_version_num"]) if rows else 0
        return self._server_version

    def _feature_supported(self, feature: str) -> bool:
        """Проверка поддержки функции и кэширование результата."""
        return self._feature_support.get(feature, True)
