"""Scan running Docker containers for database images.

Calls `docker ps` + `docker inspect` and extracts connection info from
image name, published ports, and env vars. Designed to never raise:
any failure (docker missing, daemon down, parse error, timeout) returns
an empty list so callers can treat it as a best-effort helper.
"""

from __future__ import annotations

import asyncio
import json
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


# Image name prefix -> dialect. Longer prefixes first so "mariadb" wins
# over a hypothetical "mar*" match.
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
    # MariaDB aliases both prefixes; prefer the explicit user if present.
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
    # gvenzl/oracle-xe: APP_USER / APP_USER_PASSWORD, fallback to SYS / ORACLE_PASSWORD.
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


async def _run_docker(args: list[str], timeout: float) -> str | None:
    """Run `docker` with args and return stdout, or None on any failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, NotImplementedError, OSError):
        return None
    try:
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return None
    if proc.returncode != 0:
        return None
    return stdout.decode("utf-8", errors="replace")


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


def _detect_one(
    container_id: str, image: str, name: str, inspect_raw: str,
) -> DetectedDb | None:
    """Build a DetectedDb from `docker inspect` JSON for one container."""
    dialect = _classify(image)
    if dialect is None:
        return None
    try:
        data = json.loads(inspect_raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    spec = data[0]
    env_list = (spec.get("Config") or {}).get("Env") or []
    env = _extract_env(env_list)
    ports = (spec.get("NetworkSettings") or {}).get("Ports") or {}
    default_port = DEFAULT_PORTS[dialect]
    host_port = _host_port_for(ports, default_port)
    if host_port is None:
        # Port not published to the host - skip, user can't reach it anyway.
        return None
    return _BUILDERS[dialect](env, host_port, image, name)


async def list_db_containers(timeout: float = 0.8) -> list[DetectedDb]:
    """Return DB containers running on the local Docker daemon.

    Never raises. Returns an empty list if docker is missing, the daemon
    is unreachable, the commands time out, or nothing matches.
    """
    ps_out = await _run_docker(["ps", "--format", "{{json .}}"], timeout=timeout)
    if not ps_out:
        return []
    rows = _parse_ps(ps_out)
    candidates = [(cid, img, nm) for cid, img, nm in rows if _classify(img) is not None]
    if not candidates:
        return []

    async def _inspect(cid: str, img: str, nm: str) -> DetectedDb | None:
        raw = await _run_docker(["inspect", cid], timeout=timeout)
        if not raw:
            return None
        return _detect_one(cid, img, nm, raw)

    results = await asyncio.gather(
        *(_inspect(cid, img, nm) for cid, img, nm in candidates),
        return_exceptions=True,
    )
    detected: list[DetectedDb] = []
    for r in results:
        if isinstance(r, DetectedDb):
            detected.append(r)
    return detected
