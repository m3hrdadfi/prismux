import json
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction, create_async_engine


Row = Mapping[str, Any]


def _public_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return value


class Cursor:
    def __init__(self, rows: list[dict[str, Any]], *, rowcount: int = -1, lastrowid: int | None = None):
        self._rows = rows
        self._offset = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    async def fetchone(self) -> dict[str, Any] | None:
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    async def fetchall(self) -> list[dict[str, Any]]:
        rows = self._rows[self._offset:]
        self._offset = len(self._rows)
        return rows

    async def close(self) -> None:
        return None


def _postgres_sql(sql: str) -> str:
    sql = sql.replace(" COLLATE NOCASE", "")
    sql = sql.replace("datetime('now', 'start of day')", "date_trunc('day', CURRENT_TIMESTAMP)")
    sql = sql.replace("datetime('now', ?)", "(CURRENT_TIMESTAMP + CAST(CAST(? AS text) AS interval))")
    sql = re.sub(r"\bis_default\s*=\s*0\b", "is_default = false", sql)
    sql = re.sub(r"\bis_default\s*=\s*1\b", "is_default = true", sql)
    sql = re.sub(r"\benabled\s*=\s*0\b", "enabled = false", sql)
    sql = re.sub(r"\benabled\s*=\s*1\b", "enabled = true", sql)
    sql = sql.replace("VALUES (?, 1, ?)", "VALUES (?, true, ?)")
    sql = sql.replace("r.request_payload LIKE ?", "r.request_payload ILIKE ?")
    return sql


def _bind(sql: str, params: Iterable[Any] | None) -> tuple[str, dict[str, Any]]:
    values = list(params or [])
    index = 0

    def replace(_: re.Match[str]) -> str:
        nonlocal index
        name = f"p{index}"
        index += 1
        return f":{name}"

    bound_sql = re.sub(r"\?", replace, _postgres_sql(sql))
    if index != len(values):
        raise ValueError(f"SQL expected {index} parameters but received {len(values)}")
    return bound_sql, {f"p{i}": value for i, value in enumerate(values)}


class Database:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self._transaction: ContextVar[tuple[AsyncConnection, AsyncTransaction] | None] = ContextVar(
            f"database_transaction_{id(self)}", default=None
        )

    @classmethod
    async def connect(cls, url: str, *, pool_size: int = 10, max_overflow: int = 10) -> "Database":
        if not url.startswith(("postgresql+asyncpg://", "postgresql://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL asyncpg URL")
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": "prismux,public", "timezone": "UTC"}},
        )
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return cls(engine)

    async def execute(self, sql: str, params: Iterable[Any] | None = None) -> Cursor:
        statement, values = _bind(sql, params)
        wants_id = bool(re.match(r"\s*INSERT\s+INTO\s+requests\b", statement, re.IGNORECASE))
        if wants_id and "RETURNING" not in statement.upper():
            statement = f"{statement.rstrip()} RETURNING id"
        mutation = bool(
            re.match(r"\s*(INSERT|UPDATE|DELETE)\b", statement, re.IGNORECASE)
            or (re.match(r"\s*WITH\b", statement, re.IGNORECASE) and re.search(r"\b(INSERT|UPDATE|DELETE)\b", statement, re.IGNORECASE))
        )
        state = self._transaction.get()
        created_state = False
        if mutation and state is None:
            connection = await self.engine.connect()
            transaction = await connection.begin()
            state = (connection, transaction)
            self._transaction.set(state)
            created_state = True
        if state is not None:
            connection = state[0]
            try:
                result = await connection.execute(text(statement), values)
            except Exception:
                if created_state:
                    await self.rollback()
                raise
            rows = [
                {key: _public_value(value) for key, value in row.items()}
                for row in result.mappings().all()
            ] if result.returns_rows else []
            lastrowid = int(rows[0]["id"]) if wants_id and rows else None
            return Cursor(rows, rowcount=result.rowcount, lastrowid=lastrowid)
        async with self.engine.connect() as connection:
            result = await connection.execute(text(statement), values)
            rows = [
                {key: _public_value(value) for key, value in row.items()}
                for row in result.mappings().all()
            ] if result.returns_rows else []
            return Cursor(rows, rowcount=result.rowcount)

    async def begin(self) -> None:
        if self._transaction.get() is not None:
            return
        connection = await self.engine.connect()
        transaction = await connection.begin()
        self._transaction.set((connection, transaction))

    async def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> Cursor:
        materialized = [list(row) for row in rows]
        if not materialized:
            return Cursor([], rowcount=0)
        statement, _ = _bind(sql, materialized[0])
        parameters = []
        for row in materialized:
            _, values = _bind(sql, row)
            parameters.append(values)
        state = self._transaction.get()
        created_state = False
        if state is None:
            connection = await self.engine.connect()
            transaction = await connection.begin()
            state = (connection, transaction)
            self._transaction.set(state)
            created_state = True
        try:
            result = await state[0].execute(text(statement), parameters)
        except Exception:
            if created_state:
                await self.rollback()
            raise
        return Cursor([], rowcount=result.rowcount)

    async def commit(self) -> None:
        state = self._transaction.get()
        if state is None:
            return
        self._transaction.set(None)
        connection, transaction = state
        try:
            await transaction.commit()
        finally:
            await connection.close()

    async def rollback(self) -> None:
        state = self._transaction.get()
        if state is None:
            return
        self._transaction.set(None)
        connection, transaction = state
        try:
            await transaction.rollback()
        finally:
            await connection.close()

    async def close(self) -> None:
        await self.engine.dispose()
