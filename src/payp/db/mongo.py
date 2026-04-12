"""MongoDB async driver for payp - uses motor (async pymongo wrapper)."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from payp.models import ConnectionCredential, ConnectionProfile, QueryResult

_MONGO_CONN_MARKERS = (
    "connection refused",
    "connection reset",
    "broken pipe",
    "not connected",
    "server selection timeout",
    "connection pool",
    "network timeout",
)


class MongoDriver:
    """Async MongoDB driver wrapping motor."""

    def __init__(self, profile: ConnectionProfile, credential: ConnectionCredential) -> None:
        self.profile = profile
        self.credential = credential
        self._client: Any = None
        self._db: Any = None
        self._version: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    @property
    def db_version(self) -> str | None:
        return self._version

    @staticmethod
    def _is_connection_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(m in msg for m in _MONGO_CONN_MARKERS)

    async def connect(self) -> str:
        import motor.motor_asyncio  # lazy import

        pwd = self.credential.password or ""
        user = self.profile.username or ""
        host = self.profile.host
        port = self.profile.port
        db_name = self.profile.database

        if user:
            uri = f"mongodb://{user}:{pwd}@{host}:{port}/{db_name}?authSource=admin"
        else:
            uri = f"mongodb://{host}:{port}/{db_name}"

        self._client = motor.motor_asyncio.AsyncIOMotorClient(
            uri,
            serverSelectionTimeoutMS=10_000,
            connectTimeoutMS=10_000,
        )
        self._db = self._client[db_name]
        # Ping to verify connection + get version
        info = await self._client.admin.command("serverStatus")
        self._version = info.get("version", "unknown")
        return f"MongoDB {self._version}"

    async def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None

    def _parse_mql(self, mql: str) -> dict:
        """Parse JSON envelope string."""
        try:
            return json.loads(mql)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid MongoDB query (expected JSON): {e}") from e

    async def execute_raw(
        self, mql: str, params: tuple | None = None
    ) -> list[dict[str, Any]]:
        """Execute a MongoDB operation encoded as a JSON envelope string."""
        doc = self._parse_mql(mql)
        op = doc.get("op", "").lower()
        coll_name = doc.get("collection", "")
        db = self._db

        # -- introspection ops --
        if op == "listcollections":
            names = await db.list_collection_names()
            return [{"name": n} for n in sorted(names)]

        if op == "serverinfo":
            info = await self._client.admin.command("serverStatus")
            return [{"version": info.get("version", "unknown")}]

        if op == "dbstats":
            stats = await db.command("dbStats")
            return [stats]

        if op == "collstats":
            stats = await db.command("collStats", coll_name)
            return [stats]

        if op == "indexes":
            coll = db[coll_name]
            idx_info = await coll.index_information()
            return [{"name": k, **v} for k, v in idx_info.items()]

        if op == "sample":
            size = doc.get("size", 50)
            coll = db[coll_name]
            cursor = coll.aggregate([{"$sample": {"size": size}}])
            return [row async for row in cursor]

        coll = db[coll_name]

        # -- read ops --
        if op == "find":
            filt = doc.get("filter", {})
            sort = doc.get("sort")
            limit = doc.get("limit", 100)
            skip = doc.get("skip", 0)
            projection = doc.get("projection")
            cursor = coll.find(filt, projection)
            if sort:
                cursor = cursor.sort(list(sort.items()))
            cursor = cursor.skip(skip).limit(limit)
            return [row async for row in cursor]

        if op == "aggregate":
            pipeline = doc.get("pipeline", [])
            cursor = coll.aggregate(pipeline)
            return [row async for row in cursor]

        if op == "countdocuments":
            filt = doc.get("filter", {})
            count = await coll.count_documents(filt)
            return [{"count": count}]

        if op == "distinct":
            key = doc.get("key", "_id")
            filt = doc.get("filter", {})
            values = await coll.distinct(key, filt)
            return [{key: v} for v in values]

        # -- write ops --
        if op == "insertmany":
            documents = doc.get("documents", [])
            result = await coll.insert_many(documents)
            return [
                {"acknowledged": result.acknowledged, "inserted_count": len(result.inserted_ids)}
            ]

        if op == "insertone":
            document = doc.get("document", {})
            result = await coll.insert_one(document)
            return [{"acknowledged": result.acknowledged, "inserted_id": str(result.inserted_id)}]

        if op == "updatemany":
            filt = doc.get("filter", {})
            update = doc.get("update", {})
            result = await coll.update_many(filt, update)
            return [{"acknowledged": result.acknowledged, "modified_count": result.modified_count}]

        if op == "updateone":
            filt = doc.get("filter", {})
            update = doc.get("update", {})
            result = await coll.update_one(filt, update)
            return [{"acknowledged": result.acknowledged, "modified_count": result.modified_count}]

        if op == "replaceone":
            filt = doc.get("filter", {})
            replacement = doc.get("replacement", {})
            result = await coll.replace_one(filt, replacement)
            return [{"acknowledged": result.acknowledged, "modified_count": result.modified_count}]

        if op == "deletemany":
            filt = doc.get("filter", {})
            result = await coll.delete_many(filt)
            return [{"acknowledged": result.acknowledged, "deleted_count": result.deleted_count}]

        if op == "deleteone":
            filt = doc.get("filter", {})
            result = await coll.delete_one(filt)
            return [{"acknowledged": result.acknowledged, "deleted_count": result.deleted_count}]

        # -- DDL-like ops --
        if op == "createindex":
            keys = doc.get("keys", {})
            options = doc.get("options", {})
            name = await coll.create_index(list(keys.items()), **options)
            return [{"index_name": name}]

        if op == "dropindex":
            index_name = doc.get("index_name", "")
            await coll.drop_index(index_name)
            return [{"dropped": index_name}]

        raise ValueError(f"Unsupported MongoDB op: {op!r}")

    async def stream_raw(
        self,
        mql: str,
        params: tuple | None = None,
        batch_size: int = 10_000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Stream results in batches. Only supports find/aggregate ops."""
        doc = self._parse_mql(mql)
        op = doc.get("op", "").lower()
        coll_name = doc.get("collection", "")
        coll = self._db[coll_name]

        if op == "find":
            filt = doc.get("filter", {})
            sort = doc.get("sort")
            projection = doc.get("projection")
            cursor = coll.find(filt, projection)
            if sort:
                cursor = cursor.sort(list(sort.items()))
            batch: list[dict] = []
            async for doc_item in cursor:
                batch.append(doc_item)
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch

        elif op == "aggregate":
            pipeline = doc.get("pipeline", [])
            cursor = coll.aggregate(pipeline)
            batch = []
            async for doc_item in cursor:
                batch.append(doc_item)
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch

        else:
            rows = await self.execute_raw(mql, params)
            yield rows

    async def execute(self, mql: str, limit: int = 20) -> QueryResult:
        """Execute and return QueryResult (columns + rows as lists)."""
        start = time.monotonic()
        rows_raw = await self.execute_raw(mql)
        elapsed = int((time.monotonic() - start) * 1000)

        if not rows_raw:
            return QueryResult(columns=[], rows=[], row_count=0, execution_ms=elapsed)

        # Normalize: convert ObjectId to str, remove _id
        clean_rows = []
        for row in rows_raw:
            clean = {
                k: str(v) if type(v).__name__ == "ObjectId" else v
                for k, v in row.items()
                if k != "_id"
            }
            clean_rows.append(clean)

        columns = list(clean_rows[0].keys()) if clean_rows else []
        truncated = len(clean_rows) > limit
        if truncated:
            clean_rows = clean_rows[:limit]

        rows = [[r.get(c) for c in columns] for r in clean_rows]

        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_ms=elapsed,
            truncated=truncated,
        )
