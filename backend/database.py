"""Async PostgreSQL document storage for InvestorFlip.

Property feeds do not share a rigid schema, so records are stored as JSONB while
stable identifiers and common lookup fields are indexed by PostgreSQL.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterable, List, Mapping, Optional, Tuple

import asyncpg


COLLECTION_KEYS = {
    "properties": "id",
    "tax_roll": "account_id",
    "live_sync_log": "id",
    "saved": "property_id",
    "ai_analysis": "property_id",
    "enrichment": "property_id",
    "tax_history": "property_id",
    "saved_searches": "id",
    "sync_log": "name",
}
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


async def _configure_connection(connection: asyncpg.Connection) -> None:
    await connection.set_type_codec(
        "jsonb", schema="pg_catalog", encoder=_json, decoder=json.loads, format="text"
    )


def _table(name: str) -> str:
    if name not in COLLECTION_KEYS or not _SAFE_NAME.fullmatch(name):
        raise ValueError(f"Unsupported collection: {name}")
    return f'"{name}"'


def _field_expr(field: str) -> str:
    if not _SAFE_NAME.fullmatch(field):
        raise ValueError(f"Unsupported field name: {field}")
    return f"data ->> '{field}'"


def _compile_query(query: Mapping[str, Any], params: List[Any]) -> str:
    if not query:
        return "TRUE"

    clauses: List[str] = []
    for field, value in query.items():
        if field in ("$or", "$and"):
            operator = " OR " if field == "$or" else " AND "
            parts = [_compile_query(item, params) for item in value]
            clauses.append(f"({operator.join(parts)})" if parts else "TRUE")
            continue

        expr = _field_expr(field)
        if not isinstance(value, Mapping):
            params.append({field: value})
            clauses.append(f"data @> ${len(params)}::jsonb")
            continue

        field_clauses: List[str] = []
        for operator, operand in value.items():
            if operator == "$options":
                continue
            if operator == "$in":
                choices = []
                for item in operand:
                    params.append({field: item})
                    choices.append(f"data @> ${len(params)}::jsonb")
                field_clauses.append(f"({' OR '.join(choices)})" if choices else "FALSE")
            elif operator == "$ne":
                params.append({field: operand})
                field_clauses.append(f"NOT (data @> ${len(params)}::jsonb)")
            elif operator == "$regex":
                params.append(str(operand))
                regex_op = "~*" if "i" in str(value.get("$options", "")) else "~"
                field_clauses.append(f"COALESCE({expr}, '') {regex_op} ${len(params)}")
            elif operator in ("$gt", "$lt", "$gte", "$lte"):
                op_map = {"$gt": ">", "$lt": "<", "$gte": ">=", "$lte": "<="}
                sql_op = op_map[operator]
                params.append(operand)
                field_clauses.append(
                    f"CAST(COALESCE((data #>> '{{{field}}}')::numeric, 0) AS numeric) {sql_op} ${len(params)}::numeric"
                )
            elif operator == "$exists":
                params.append({field: operand})
                if operand:
                    field_clauses.append(f"data @> ${len(params)}::jsonb")
                else:
                    field_clauses.append(f"NOT (data @> ${len(params)}::jsonb)")
            else:
                raise ValueError(f"Unsupported query operator: {operator}")
        clauses.append(f"({' AND '.join(field_clauses)})" if field_clauses else "TRUE")

    return " AND ".join(clauses) if clauses else "TRUE"


def _simple_filter_values(query: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in query.items()
        if not key.startswith("$") and not isinstance(value, Mapping)
    }


def _project(document: Dict[str, Any], projection: Optional[Mapping[str, int]]) -> Dict[str, Any]:
    if not projection:
        return document
    included = {key for key, enabled in projection.items() if enabled and key != "_id"}
    if included:
        return {key: document[key] for key in included if key in document}
    excluded = {key for key, enabled in projection.items() if not enabled}
    return {key: value for key, value in document.items() if key not in excluded}


@dataclass
class WriteResult:
    deleted_count: int = 0
    modified_count: int = 0
    upserted_count: int = 0


class PostgresCursor:
    def __init__(
        self,
        collection: "PostgresCollection",
        query: Mapping[str, Any],
        projection: Optional[Mapping[str, int]],
    ) -> None:
        self.collection = collection
        self.query = query
        self.projection = projection
        self.sort_field: Optional[str] = None
        self.sort_direction = -1
        self.row_limit: Optional[int] = None

    def sort(self, field: str, direction: int) -> "PostgresCursor":
        self.sort_field = field
        self.sort_direction = direction
        return self

    def limit(self, limit: int) -> "PostgresCursor":
        self.row_limit = limit
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        limit = min(filter(None, [self.row_limit, length]), default=None)
        return await self.collection._find(
            self.query, self.projection, self.sort_field, self.sort_direction, limit
        )

    def __aiter__(self) -> AsyncIterator[Dict[str, Any]]:
        async def iterate() -> AsyncIterator[Dict[str, Any]]:
            for document in await self.to_list():
                yield document

        return iterate()


class PostgresCollection:
    def __init__(self, database: "PostgresDatabase", name: str) -> None:
        self.database = database
        self.name = name
        self.key_field = COLLECTION_KEYS[name]

    async def _pool(self) -> asyncpg.Pool:
        return await self.database.connect()

    def find(
        self,
        query: Optional[Mapping[str, Any]] = None,
        projection: Optional[Mapping[str, int]] = None,
    ) -> PostgresCursor:
        return PostgresCursor(self, query or {}, projection)

    async def _find(
        self,
        query: Mapping[str, Any],
        projection: Optional[Mapping[str, int]],
        sort_field: Optional[str],
        sort_direction: int,
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        params: List[Any] = []
        where = _compile_query(query, params)
        sql = f"SELECT data FROM {_table(self.name)} WHERE {where}"
        if sort_field:
            sql += f" ORDER BY {_field_expr(sort_field)} {'ASC' if sort_direction >= 0 else 'DESC'} NULLS LAST"
        if limit is not None:
            params.append(limit)
            sql += f" LIMIT ${len(params)}"
        rows = await (await self._pool()).fetch(sql, *params)
        return [_project(dict(row["data"]), projection) for row in rows]

    async def find_one(
        self,
        query: Mapping[str, Any],
        projection: Optional[Mapping[str, int]] = None,
    ) -> Optional[Dict[str, Any]]:
        documents = await self._find(query, projection, None, -1, 1)
        return documents[0] if documents else None

    async def count_documents(self, query: Mapping[str, Any]) -> int:
        params: List[Any] = []
        where = _compile_query(query, params)
        return int(await (await self._pool()).fetchval(
            f"SELECT count(*) FROM {_table(self.name)} WHERE {where}", *params
        ))

    async def insert_one(self, document: Mapping[str, Any]) -> WriteResult:
        item = dict(document)
        key = str(item.get(self.key_field) or uuid.uuid4())
        item[self.key_field] = key
        await (await self._pool()).execute(
            f"INSERT INTO {_table(self.name)} (document_key, data) VALUES ($1, $2::jsonb)",
            key,
            item,
        )
        return WriteResult(modified_count=1)

    async def insert_many(self, documents: Iterable[Mapping[str, Any]]) -> WriteResult:
        rows: List[Tuple[str, Dict[str, Any]]] = []
        for document in documents:
            item = dict(document)
            key = str(item.get(self.key_field) or uuid.uuid4())
            item[self.key_field] = key
            rows.append((key, item))
        if rows:
            await (await self._pool()).executemany(
                f"INSERT INTO {_table(self.name)} (document_key, data) VALUES ($1, $2::jsonb)", rows
            )
        return WriteResult(modified_count=len(rows))

    async def upsert_many(self, documents: Iterable[Mapping[str, Any]]) -> WriteResult:
        rows: List[Tuple[str, Dict[str, Any]]] = []
        for document in documents:
            item = dict(document)
            key = str(item.get(self.key_field) or uuid.uuid4())
            item[self.key_field] = key
            rows.append((key, item))
        if rows:
            table = _table(self.name)
            await (await self._pool()).executemany(
                f"""
                INSERT INTO {table} (document_key, data) VALUES ($1, $2::jsonb)
                ON CONFLICT (document_key) DO UPDATE
                SET data = {table}.data || EXCLUDED.data, updated_at = now()
                """,
                rows,
            )
        return WriteResult(modified_count=len(rows))

    async def update_one(
        self,
        query: Mapping[str, Any],
        update: Mapping[str, Mapping[str, Any]],
        upsert: bool = False,
    ) -> WriteResult:
        existing = await self.find_one(query)
        changes = dict(update.get("$set", {}))
        if existing:
            key = str(existing[self.key_field])
            await (await self._pool()).execute(
                f"UPDATE {_table(self.name)} SET data = data || $2::jsonb, updated_at = now() WHERE document_key = $1",
                key,
                changes,
            )
            return WriteResult(modified_count=1)
        if not upsert:
            return WriteResult()

        document = _simple_filter_values(query)
        document.update(update.get("$setOnInsert", {}))
        document.update(changes)
        await self.insert_one(document)
        return WriteResult(upserted_count=1)

    async def delete_one(self, query: Mapping[str, Any]) -> WriteResult:
        existing = await self.find_one(query)
        if not existing:
            return WriteResult()
        status = await (await self._pool()).execute(
            f"DELETE FROM {_table(self.name)} WHERE document_key = $1",
            str(existing[self.key_field]),
        )
        return WriteResult(deleted_count=int(status.rsplit(" ", 1)[-1]))

    async def delete_many(self, query: Mapping[str, Any]) -> WriteResult:
        params: List[Any] = []
        where = _compile_query(query, params)
        status = await (await self._pool()).execute(
            f"DELETE FROM {_table(self.name)} WHERE {where}", *params
        )
        return WriteResult(deleted_count=int(status.rsplit(" ", 1)[-1]))

    async def create_index(self, *_args: Any, **_kwargs: Any) -> None:
        await self.database.connect()


class PostgresDatabase:
    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url or os.environ.get("DATABASE_URL", "").strip()
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required")
        self._pool_instance: Optional[asyncpg.Pool] = None

    def __getattr__(self, name: str) -> PostgresCollection:
        if name in COLLECTION_KEYS:
            return PostgresCollection(self, name)
        raise AttributeError(name)

    async def connect(self) -> asyncpg.Pool:
        if self._pool_instance is None:
            self._pool_instance = await asyncpg.create_pool(
                self.database_url, min_size=1, max_size=10, init=_configure_connection
            )
            await self._initialize()
        return self._pool_instance

    async def _initialize(self) -> None:
        assert self._pool_instance is not None
        async with self._pool_instance.acquire() as connection:
            for name in COLLECTION_KEYS:
                await connection.execute(f"""
                    CREATE TABLE IF NOT EXISTS {_table(name)} (
                        document_key text PRIMARY KEY,
                        data jsonb NOT NULL,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now()
                    )
                """)
                await connection.execute(
                    f'CREATE INDEX IF NOT EXISTS "{name}_data_gin" ON {_table(name)} USING gin (data)'
                )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS properties_updated_at_idx ON properties ((data ->> 'updated_at') DESC)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS properties_address_idx ON properties ((lower(data ->> 'situs_address')))"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS properties_account_idx ON properties ((data ->> 'account_id'))"
            )

    async def close(self) -> None:
        if self._pool_instance is not None:
            await self._pool_instance.close()
            self._pool_instance = None
