"""Tables and views — listing and details (used only by objects module)."""

from typing import Any

from postgres_fastmcp.common.utils import decode_bytes_to_utf8
from postgres_fastmcp.enums import UserRole
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.sql.catalog.queries import (
    QUERY_GET_COLUMNS,
    QUERY_GET_CONSTRAINTS,
    QUERY_GET_INDEXES,
    QUERY_LIST_TABLES_VIEWS,
)


class TablesService:
    """Service for listing tables/views and getting their details."""

    def __init__(self, db: DbAccessService) -> None:
        self.db = db

    async def list_tables_views(
        self,
        schema_name: str,
        object_type: str = "table",
    ) -> list[dict[str, Any]]:
        """List tables or views in a schema."""
        table_type = "BASE TABLE" if object_type == "table" else "VIEW"
        sql_driver = self.db.sql_driver
        rows = await sql_driver.execute(
            QUERY_LIST_TABLES_VIEWS,
            params=[schema_name, table_type],
            readonly=True,
        )
        objects = (
            [
                {
                    "schema": decode_bytes_to_utf8(row.cells["table_schema"]),
                    "name": decode_bytes_to_utf8(row.cells["table_name"]),
                    "type": decode_bytes_to_utf8(row.cells["table_type"]),
                }
                for row in rows
            ]
            if rows
            else []
        )
        if self.db.role == UserRole.USER and self.db.config.table_prefix:
            prefix = self.db.config.table_prefix.lower()
            objects = [o for o in objects if o["name"].lower().startswith(prefix)]
        return objects

    async def get_details(
        self,
        schema_name: str,
        object_name: str,
        object_type: str = "table",
    ) -> dict[str, Any]:
        """Get columns, constraints, indexes for a table or view."""
        sql_driver = self.db.sql_driver

        col_rows = await sql_driver.execute(QUERY_GET_COLUMNS, params=[schema_name, object_name], readonly=True)
        columns = (
            [
                {
                    "column": decode_bytes_to_utf8(r.cells["column_name"]),
                    "data_type": decode_bytes_to_utf8(r.cells["data_type"]),
                    "is_nullable": decode_bytes_to_utf8(r.cells["is_nullable"]),
                    "default": decode_bytes_to_utf8(r.cells["column_default"]),
                }
                for r in col_rows
            ]
            if col_rows
            else []
        )

        con_rows = await sql_driver.execute(QUERY_GET_CONSTRAINTS, params=[schema_name, object_name], readonly=True)
        constraints: dict[str, dict[str, Any]] = {}
        if con_rows:
            for row in con_rows:
                cname = decode_bytes_to_utf8(row.cells["constraint_name"])
                ctype = decode_bytes_to_utf8(row.cells["constraint_type"])
                col = decode_bytes_to_utf8(row.cells["column_name"])
                if isinstance(cname, str) and isinstance(ctype, (str, int, float, bool, type(None))):
                    if cname not in constraints:
                        constraints[cname] = {"type": ctype, "columns": []}
                    if col and isinstance(col, str):
                        constraints[cname]["columns"].append(col)
        constraints_list = [{"name": k, **v} for k, v in constraints.items()]

        idx_rows = await sql_driver.execute(QUERY_GET_INDEXES, params=[schema_name, object_name], readonly=True)
        indexes = (
            [
                {
                    "name": decode_bytes_to_utf8(r.cells["indexname"]),
                    "definition": decode_bytes_to_utf8(r.cells["indexdef"]),
                }
                for r in idx_rows
            ]
            if idx_rows
            else []
        )

        return {
            "basic": {"schema": schema_name, "name": object_name, "type": object_type},
            "columns": columns,
            "constraints": constraints_list,
            "indexes": indexes,
        }
