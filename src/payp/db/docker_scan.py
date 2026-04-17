"""Scan running Docker containers for database images.

Calls `docker ps` + `docker inspect` and extracts connection info from
image name, published ports, and env vars. Designed to never raise:
any failure (docker missing, daemon down, parse error, timeout) returns
an empty list so callers can treat it as a best-effort helper.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass

from payp.models import DbType


@dataclass(frozen=True)
class DetectedDb:
    """A DB container detected in the local Docker daemon."""

    image: str
    container_name: str
    db_type: DbType
    host: str
    port: int
    database: str
    username: str
    password: str | None


# Longer prefixes first so "mariadb" is matched before "mar" ever could be.
IMAGE_MAP: tuple[tuple[str, DbType], ...] = (
    ("mariadb", DbType.MYSQL),
    ("mysql", DbType.MYSQL),
    ("postgis/postgis", DbType.POSTGRESQL),
    ("postgres", DbType.POSTGRESQL),
    ("gvenzl/oracle", DbType.ORACLE),
    ("container-registry.oracle.com/database", DbType.ORACLE),
    ("bitnami/mongodb", DbType.MONGODB),
    ("mongo", DbType.MONGODB),
)


DEFAULT_PORTS: dict[DbType, int] = {
    DbType.POSTGRESQL: 5432,
    DbType.MYSQL: 3306,
    DbType.ORACLE: 1521,
    DbType.MONGODB: 27017,
}


def _classify(image: str) -> DbType | None:
    """Return the dialect for an image name, or None if unknown."""
    name = image.lower()
    bare = name.split(":", 1)[0]
    for prefix, dialect in IMAGE_MAP:
        if bare == prefix or bare.startswith(prefix + "/") or bare.endswith("/" + prefix):
            return dialect
    for prefix, dialect in IMAGE_MAP:
        if prefix in bare:
            return dialect
    return None


def _extract_env(env_list: list[str]) -> dict[str, str]:
    """Parse a Docker env list ([\"KEY=value\", ...]) into a dict."""
    out: dict[str, str] = {}
    for item in env_list:
        if "=" in item:
            k, v = item.split("=", 1)
            out[k] = v
    return out


def _host_port_for(
    ports: dict[str, list[dict[str, str]] | None] | None,
    container_port: int,
) -> int | None:
    """Find the host port mapped to a given container port (tcp).

    ``ports`` is the shape Docker returns under ``NetworkSettings.Ports``:
    ``{\"5432/tcp\": [{\"HostIp\": \"0.0.0.0\", \"HostPort\": \"5433\"}]}``.
    Returns None if the port is not published on the host.
    """
    if not ports:
        return None
    key = f"{container_port}/tcp"
    bindings = ports.get(key)
    if not bindings:
        return None
    for binding in bindings:
        raw = binding.get("HostPort")
        if raw and raw.isdigit():
            return int(raw)
    return None


def _build_postgres(env: dict[str, str], port: int, image: str, name: str) -> DetectedDb:
    user = env.get("POSTGRES_USER", "postgres")
    database = env.get("POSTGRES_DB", user)
    password = env.get("POSTGRES_PASSWORD") or None
    return DetectedDb(
        image=image, container_name=name, db_type=DbType.POSTGRESQL,
        host="localhost", port=port, database=database, username=user, password=password,
    )


def _build_mysql(env: dict[str, str], port: int, image: str, name: str) -> DetectedDb:
    user = env.get("MYSQL_USER") or env.get("MARIADB_USER") or "root"
    database = (
        env.get("MYSQL_DATABASE") or env.get("MARIADB_DATABASE") or "mysql"
    )
    if user == "root":
        password = (
            env.get("MYSQL_ROOT_PASSWORD")
            or env.get("MARIADB_ROOT_PASSWORD")
            or None
        )
    else:
        password = (
            env.get("MYSQL_PASSWORD") or env.get("MARIADB_PASSWORD") or None
        )
    return DetectedDb(
        image=image, container_name=name, db_type=DbType.MYSQL,
        host="localhost", port=port, database=database, username=user, password=password,
    )


def _build_oracle(env: dict[str, str], port: int, image: str, name: str) -> DetectedDb:
    app_user = env.get("APP_USER")
    if app_user:
        user = app_user
        password = env.get("APP_USER_PASSWORD") or None
    else:
        user = "SYS"
        password = env.get("ORACLE_PASSWORD") or None
    database = env.get("ORACLE_DATABASE", "XEPDB1")
    return DetectedDb(
        image=image, container_name=name, db_type=DbType.ORACLE,
        host="localhost", port=port, database=database, username=user, password=password,
    )


def _build_mongo(env: dict[str, str], port: int, image: str, name: str) -> DetectedDb:
    user = env.get("MONGO_INITDB_ROOT_USERNAME", "")
    password = env.get("MONGO_INITDB_ROOT_PASSWORD") or None
    database = env.get("MONGO_INITDB_DATABASE", "admin")
    return DetectedDb(
        image=image, container_name=name, db_type=DbType.MONGODB,
        host="localhost", port=port, database=database, username=user, password=password,
    )


_BUILDERS = {
    DbType.POSTGRESQL: _build_postgres,
    DbType.MYSQL: _build_mysql,
    DbType.ORACLE: _build_oracle,
    DbType.MONGODB: _build_mongo,
}


def _run_docker_sync(args: list[str], timeout: float) -> str | None:
    """Synchronous `docker` call. Returns stdout or None on any failure."""
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


async def _run_docker(args: list[str], timeout: float) -> str | None:
    """Run `docker` off the event loop.

    Uses a worker thread + blocking ``subprocess.run`` because payp forces
    ``WindowsSelectorEventLoopPolicy`` (for psycopg3), and the Selector loop
    on Windows does not support ``create_subprocess_exec``.
    """
    return await asyncio.to_thread(_run_docker_sync, args, timeout)


def _parse_ps(raw: str) -> list[tuple[str, str, str]]:
    """Parse `docker ps --format '{{json .}}'` output.

    Returns list of (container_id, image, name). Non-JSON lines are skipped.
    """
    out: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = row.get("ID") or row.get("Id") or ""
        image = row.get("Image") or ""
        name = row.get("Names") or row.get("Name") or cid
        if cid and image:
            out.append((cid, image, name))
    return out


def _detect_from_spec(image: str, name: str, spec: dict) -> DetectedDb | None:
    """Build a DetectedDb from a parsed `docker inspect` spec dict."""
    dialect = _classify(image)
    if dialect is None:
        return None
    env_list = (spec.get("Config") or {}).get("Env") or []
    env = _extract_env(env_list)
    ports = (spec.get("NetworkSettings") or {}).get("Ports") or {}
    default_port = DEFAULT_PORTS[dialect]
    host_port = _host_port_for(ports, default_port)
    if host_port is None:
        return None
    return _BUILDERS[dialect](env, host_port, image, name)


def _detect_one(
    container_id: str, image: str, name: str, inspect_raw: str,
) -> DetectedDb | None:
    """Build a DetectedDb from `docker inspect` JSON for one container."""
    try:
        data = json.loads(inspect_raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    return _detect_from_spec(image, name, data[0])


async def list_db_containers(timeout: float = 5.0) -> list[DetectedDb]:
    """Return DB containers running on the local Docker daemon.

    Never raises. Returns an empty list if docker is missing, the daemon
    is unreachable, the commands time out, or nothing matches.

    Uses a single batched `docker inspect` call across all candidates rather
    than one per container - on Windows each inspect can take 2-3s, so the
    old N-parallel path blew past aggressive timeouts.
    """
    ps_out = await _run_docker(["ps", "--format", "{{json .}}"], timeout=timeout)
    if not ps_out:
        return []
    rows = _parse_ps(ps_out)
    candidates = [(cid, img, nm) for cid, img, nm in rows if _classify(img) is not None]
    if not candidates:
        return []

    inspect_raw = await _run_docker(
        ["inspect", *(cid for cid, _, _ in candidates)], timeout=timeout,
    )
    if not inspect_raw:
        return []
    try:
        specs = json.loads(inspect_raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(specs, list) or len(specs) != len(candidates):
        return []

    detected: list[DetectedDb] = []
    for (_cid, img, nm), spec in zip(candidates, specs):
        if not isinstance(spec, dict):
            continue
        d = _detect_from_spec(img, nm, spec)
        if d is not None:
            detected.append(d)
    return detected
