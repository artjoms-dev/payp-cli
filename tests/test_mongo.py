"""Integration tests for MongoDB support.

Requires: docker-compose up -d mongo
"""
from __future__ import annotations

import json

import pytest

from payp.db.connection import ConnectionManager
from payp.db.introspection import discover_t0, discover_t1, discover_t2
from payp.models import ConnectionCredential, ConnectionProfile, DbType

PROFILE = ConnectionProfile(
    name="test_mongo",
    db_type=DbType.MONGODB,
    host="localhost",
    port=27017,
    database="payp_test",
    username="payp",
)
CREDENTIAL = ConnectionCredential(password="payp_dev")


@pytest.fixture
async def conn():
    mgr = ConnectionManager(PROFILE, CREDENTIAL)
    await mgr.connect()
    yield mgr
    await mgr.disconnect()


@pytest.mark.asyncio
async def test_connect():
    mgr = ConnectionManager(PROFILE, CREDENTIAL)
    version = await mgr.connect()
    assert "MongoDB" in version
    assert mgr.is_connected
    await mgr.disconnect()
    assert not mgr.is_connected


@pytest.mark.asyncio
async def test_t0_discover(conn):
    t0 = await discover_t0(conn)
    assert t0.total_tables >= 3
    assert "payp_test" in t0.schemas
    assert "MongoDB" in t0.db_version


@pytest.mark.asyncio
async def test_t1_discover(conn):
    t1 = await discover_t1(conn)
    colls = t1.tables.get("payp_test", [])
    assert "customers" in colls
    assert "products" in colls
    assert "orders" in colls


@pytest.mark.asyncio
async def test_t2_customers(conn):
    ddl = await discover_t2(conn, "payp_test", "customers")
    assert "Collection: customers" in ddl
    assert "name" in ddl
    assert "email" in ddl


@pytest.mark.asyncio
async def test_find_all_customers(conn):
    rows = await conn.execute_raw(json.dumps({
        "op": "find", "collection": "customers", "filter": {}, "limit": 10
    }))
    # Limit honoured; seed guarantees at least a handful of customers.
    assert 1 <= len(rows) <= 10
    assert "name" in rows[0]
    assert "email" in rows[0]


@pytest.mark.asyncio
async def test_find_with_filter(conn):
    rows = await conn.execute_raw(json.dumps({
        "op": "find",
        "collection": "customers",
        "filter": {"region": "EU-West"},
        "limit": 10,
    }))
    assert all(r["region"] == "EU-West" for r in rows)
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_aggregate_orders_by_status(conn):
    rows = await conn.execute_raw(json.dumps({
        "op": "aggregate",
        "collection": "orders",
        "pipeline": [
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ],
    }))
    statuses = {r["_id"] for r in rows}
    assert "completed" in statuses


@pytest.mark.asyncio
async def test_count_documents(conn):
    rows = await conn.execute_raw(json.dumps({
        "op": "countDocuments", "collection": "customers", "filter": {}
    }))
    # Seed uses Faker-generated data; just verify non-empty.
    assert rows[0]["count"] >= 1


@pytest.mark.asyncio
async def test_insert_and_delete(conn):
    ins = await conn.execute_raw(json.dumps({
        "op": "insertOne",
        "collection": "customers",
        "document": {"name": "Test User", "email": "test_tmp@example.com", "region": "TEST"},
    }))
    assert ins[0]["acknowledged"] is True

    rows = await conn.execute_raw(json.dumps({
        "op": "find", "collection": "customers",
        "filter": {"email": "test_tmp@example.com"},
    }))
    assert len(rows) == 1

    deleted = await conn.execute_raw(json.dumps({
        "op": "deleteOne", "collection": "customers",
        "filter": {"email": "test_tmp@example.com"},
    }))
    assert deleted[0]["deleted_count"] == 1


@pytest.mark.asyncio
async def test_update(conn):
    upd = await conn.execute_raw(json.dumps({
        "op": "updateMany",
        "collection": "orders",
        "filter": {"status": "pending"},
        "update": {"$set": {"status": "processing"}},
    }))
    assert upd[0]["acknowledged"] is True

    # Restore
    await conn.execute_raw(json.dumps({
        "op": "updateMany",
        "collection": "orders",
        "filter": {"status": "processing"},
        "update": {"$set": {"status": "pending"}},
    }))


@pytest.mark.asyncio
async def test_list_collections(conn):
    rows = await conn.execute_raw('{"op": "listCollections"}')
    names = {r["name"] for r in rows}
    assert "customers" in names


@pytest.mark.asyncio
async def test_indexes(conn):
    rows = await conn.execute_raw(json.dumps({
        "op": "indexes", "collection": "customers"
    }))
    idx_names = {r["name"] for r in rows}
    assert "_id_" in idx_names
    assert "email_1" in idx_names
