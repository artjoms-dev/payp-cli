"""Pydantic data models for payp."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class DbType(str, Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    ORACLE = "oracle"
    MONGODB = "mongodb"


class SecurityMode(str, Enum):
    MANUAL = "manual"
    YOLO = "yolo"
    SECURE = "secure"
    SECURE_AUTO = "secure-auto"


class ConnectionProfile(BaseModel):
    """Database connection profile (shareable, no secrets)."""

    name: str
    db_type: DbType
    host: str
    port: int
    database: str
    username: str
    ssl: bool = False
    schema_name: str | None = None
    timeout: int = 30


class ConnectionCredential(BaseModel):
    """Database credentials (local only, never in git)."""

    password: str | None = None
    token: str | None = None
    key_file: Path | None = None


class ModelProvider(BaseModel):
    """AI model provider configuration."""

    api_key: str
    base_url: str | None = None
    default_model: str | None = None
    api_version: str | None = None


class ModelRoles(BaseModel):
    """Executor and reviewer model assignments."""

    executor: str = "openrouter/google/gemma-4-26b-a4b-it:free"
    reviewer: str | None = None  # None = no reviewer configured


class MemoryConfig(BaseModel):
    """Memory backend configuration."""

    backend: str = "native"  # "native" or "mempalace"
    mempalace_dir: str = "~/.payp/mempalace"  # where mempalace stores data


class AppConfig(BaseModel):
    """Global payp configuration."""

    default_mode: SecurityMode = SecurityMode.MANUAL
    default_model: str = "openrouter/google/gemma-4-26b-a4b-it:free"
    theme: str = "default"
    schema_budget: int = 10000
    max_session_cost_usd: float | None = None  # None = no limit
    roles: ModelRoles = Field(default_factory=ModelRoles)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)


class QueryResult(BaseModel):
    """Result from a SQL query execution."""

    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    execution_ms: int
    truncated: bool = False
    total_rows: int | None = None


class SchemaIndex(BaseModel):
    """T0 — schema-level summary."""

    db_version: str
    schemas: dict[str, int]  # schema_name -> table_count
    view_count: int = 0
    total_tables: int = 0


class SchemaCatalog(BaseModel):
    """T1 — all table names by schema."""

    tables: dict[str, list[str]]  # schema_name -> [table_names]


class SchemaGraph(BaseModel):
    """FK adjacency graph for the whole database — sits between T1 and T2.

    Built once per connection (or when T1 table list changes) and cached
    alongside T0/T1. Gives the LLM a full FK map upfront so it can plan
    multi-table JOINs without per-table tool calls.
    """

    edges: list[tuple[str, str, str, str]] = []
    # each edge: (from_schema_table, from_col, to_schema_table, to_col)
    table_names_hash: str = ""
    # sha1 of sorted "schema.table" list from T1 — cheap drift detector


class CostTracker(BaseModel):
    """Token usage and cost tracking for a session."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    query_count: int = 0
