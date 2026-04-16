"""Unit tests for payp.db.docker_scan.

Mocks `asyncio.create_subprocess_exec` so tests run without a real Docker
daemon. Covers: 4 dialects, docker missing, daemon down, timeout, image
classification, port-not-published skip.
"""

from __future__ import annotations

import asyncio
import json

from payp.db import docker_scan
from payp.models import DbType

# --- _classify ---

def test_classify_exact():
    assert docker_scan._classify("postgres:15") == DbType.POSTGRESQL
    assert docker_scan._classify("mysql:8") == DbType.MYSQL
    assert docker_scan._classify("mariadb:11") == DbType.MYSQL
    assert docker_scan._classify("mongo:7") == DbType.MONGODB
    assert docker_scan._classify("gvenzl/oracle-xe:21") == DbType.ORACLE


def test_classify_unknown():
    assert docker_scan._classify("redis:7") is None
    assert docker_scan._classify("nginx") is None
    assert docker_scan._classify("") is None


def test_classify_substring_fallback():
    assert docker_scan._classify("mycompany/postgres-fork:1.0") == DbType.POSTGRESQL
    assert docker_scan._classify("bitnami/mongodb:6") == DbType.MONGODB


# --- _extract_env ---

def test_extract_env():
    env = docker_scan._extract_env(
        ["FOO=bar", "BAZ=qux=extra", "INVALID", "EMPTY="]
    )
    assert env == {"FOO": "bar", "BAZ": "qux=extra", "EMPTY": ""}


# --- _host_port_for ---

def test_host_port_published():
    ports = {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5433"}]}
    assert docker_scan._host_port_for(ports, 5432) == 5433


def test_host_port_not_published():
    ports = {"5432/tcp": None}
    assert docker_scan._host_port_for(ports, 5432) is None
    assert docker_scan._host_port_for({}, 5432) is None
    assert docker_scan._host_port_for(None, 5432) is None


def test_host_port_wrong_dialect():
    ports = {"3306/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3306"}]}
    assert docker_scan._host_port_for(ports, 5432) is None


# --- builders ---

def _inspect_json(env: dict, host_port: int, container_port: int) -> str:
    return json.dumps([{
        "Config": {"Env": [f"{k}={v}" for k, v in env.items()]},
        "NetworkSettings": {
            "Ports": {
                f"{container_port}/tcp": [
                    {"HostIp": "0.0.0.0", "HostPort": str(host_port)}
                ]
            }
        },
    }])


def test_detect_postgres():
    raw = _inspect_json(
        {"POSTGRES_USER": "app", "POSTGRES_PASSWORD": "s3cret", "POSTGRES_DB": "shop"},
        5433, 5432,
    )
    d = docker_scan._detect_one("cid", "postgres:15", "pg-dev", raw)
    assert d is not None
    assert d.db_type == DbType.POSTGRESQL
    assert d.port == 5433
    assert d.username == "app"
    assert d.password == "s3cret"
    assert d.database == "shop"
    assert d.container_name == "pg-dev"


def test_detect_postgres_defaults():
    raw = _inspect_json({}, 5432, 5432)
    d = docker_scan._detect_one("cid", "postgres:15", "pg", raw)
    assert d is not None
    assert d.username == "postgres"
    assert d.database == "postgres"
    assert d.password is None


def test_detect_mysql_root():
    raw = _inspect_json(
        {"MYSQL_ROOT_PASSWORD": "root-pw", "MYSQL_DATABASE": "shop"}, 3307, 3306,
    )
    d = docker_scan._detect_one("cid", "mysql:8", "my", raw)
    assert d is not None
    assert d.username == "root"
    assert d.password == "root-pw"
    assert d.database == "shop"


def test_detect_mysql_user():
    raw = _inspect_json(
        {"MYSQL_USER": "app", "MYSQL_PASSWORD": "u-pw", "MYSQL_DATABASE": "shop"},
        3306, 3306,
    )
    d = docker_scan._detect_one("cid", "mysql:8", "my", raw)
    assert d is not None
    assert d.username == "app"
    assert d.password == "u-pw"


def test_detect_mariadb_alias():
    raw = _inspect_json(
        {"MARIADB_USER": "app", "MARIADB_PASSWORD": "u-pw", "MARIADB_DATABASE": "shop"},
        3306, 3306,
    )
    d = docker_scan._detect_one("cid", "mariadb:11", "md", raw)
    assert d is not None
    assert d.db_type == DbType.MYSQL
    assert d.username == "app"
    assert d.password == "u-pw"


def test_detect_oracle_app_user():
    raw = _inspect_json(
        {"APP_USER": "shop", "APP_USER_PASSWORD": "pw", "ORACLE_DATABASE": "FREEPDB1"},
        1521, 1521,
    )
    d = docker_scan._detect_one("cid", "gvenzl/oracle-xe:21", "ox", raw)
    assert d is not None
    assert d.db_type == DbType.ORACLE
    assert d.username == "shop"
    assert d.password == "pw"
    assert d.database == "FREEPDB1"


def test_detect_oracle_sys_fallback():
    raw = _inspect_json({"ORACLE_PASSWORD": "sys-pw"}, 1521, 1521)
    d = docker_scan._detect_one("cid", "gvenzl/oracle-xe:21", "ox", raw)
    assert d is not None
    assert d.username == "SYS"
    assert d.password == "sys-pw"
    assert d.database == "XEPDB1"


def test_detect_mongo():
    raw = _inspect_json(
        {
            "MONGO_INITDB_ROOT_USERNAME": "admin",
            "MONGO_INITDB_ROOT_PASSWORD": "pw",
            "MONGO_INITDB_DATABASE": "shop",
        },
        27018, 27017,
    )
    d = docker_scan._detect_one("cid", "mongo:7", "mg", raw)
    assert d is not None
    assert d.db_type == DbType.MONGODB
    assert d.username == "admin"
    assert d.password == "pw"
    assert d.database == "shop"


def test_detect_unknown_image_returns_none():
    raw = _inspect_json({}, 6379, 6379)
    assert docker_scan._detect_one("cid", "redis:7", "r", raw) is None


def test_detect_port_not_published_returns_none():
    raw = json.dumps([{
        "Config": {"Env": ["POSTGRES_PASSWORD=pw"]},
        "NetworkSettings": {"Ports": {"5432/tcp": None}},
    }])
    assert docker_scan._detect_one("cid", "postgres:15", "p", raw) is None


def test_detect_bad_json_returns_none():
    assert docker_scan._detect_one("cid", "postgres:15", "p", "{not json") is None


# --- _parse_ps ---

def test_parse_ps():
    raw = (
        '{"ID":"abc","Image":"postgres:15","Names":"pg-dev"}\n'
        '{"ID":"def","Image":"mysql:8","Names":"my"}\n'
        "not json line\n"
        "\n"
    )
    rows = docker_scan._parse_ps(raw)
    assert rows == [
        ("abc", "postgres:15", "pg-dev"),
        ("def", "mysql:8", "my"),
    ]


# --- list_db_containers integration with subprocess mocked ---

class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0, hang: bool = False):
        self._stdout = stdout
        self.returncode = returncode
        self._hang = hang

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(10)
        return (self._stdout, b"")

    def kill(self):
        pass


def _make_subprocess(responses: dict[str, _FakeProc]):
    """Build a fake create_subprocess_exec that dispatches on first arg."""
    async def fake(*args, **kwargs):  # noqa: ARG001
        key = args[1] if len(args) > 1 else ""
        return responses.get(key) or _FakeProc(b"", returncode=1)
    return fake


def test_list_no_docker(monkeypatch):
    async def boom(*args, **kwargs):
        raise FileNotFoundError
    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    assert asyncio.run(docker_scan.list_db_containers(timeout=0.1)) == []


def test_list_daemon_down(monkeypatch):
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec",
        _make_subprocess({"ps": _FakeProc(b"", returncode=1)}),
    )
    assert asyncio.run(docker_scan.list_db_containers(timeout=0.1)) == []


def test_list_ps_timeout(monkeypatch):
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec",
        _make_subprocess({"ps": _FakeProc(b"", hang=True)}),
    )
    assert asyncio.run(docker_scan.list_db_containers(timeout=0.05)) == []


def test_list_returns_postgres(monkeypatch):
    ps_out = (
        b'{"ID":"abc","Image":"postgres:15","Names":"pg-dev"}\n'
    )
    inspect_out = _inspect_json(
        {"POSTGRES_USER": "app", "POSTGRES_PASSWORD": "pw", "POSTGRES_DB": "shop"},
        5433, 5432,
    ).encode()
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec",
        _make_subprocess({
            "ps": _FakeProc(ps_out),
            "inspect": _FakeProc(inspect_out),
        }),
    )
    result = asyncio.run(docker_scan.list_db_containers(timeout=1.0))
    assert len(result) == 1
    assert result[0].db_type == DbType.POSTGRESQL
    assert result[0].port == 5433
    assert result[0].password == "pw"


def test_list_filters_non_db(monkeypatch):
    ps_out = (
        b'{"ID":"abc","Image":"nginx:latest","Names":"web"}\n'
        b'{"ID":"def","Image":"redis:7","Names":"cache"}\n'
    )
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec",
        _make_subprocess({"ps": _FakeProc(ps_out)}),
    )
    assert asyncio.run(docker_scan.list_db_containers(timeout=1.0)) == []
