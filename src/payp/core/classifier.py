"""SQL classifier — detect operation type and risk level.

Used by security modes to decide which flow to apply.

Two layers:
1. `classify_sql` — general routing (SELECT vs DML_WRITE vs DDL vs ...)
2. `statically_hard_blocked` — deterministic pre-filter for unambiguously
   dangerous patterns. Runs before the LLM reviewer so a poisoned model
   cannot approve away known-catastrophic operations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

import sqlglot
from sqlglot import exp


class SqlCategory(str, Enum):
    SELECT = "select"           # Always safe, bypass review
    DML_WRITE = "dml_write"     # INSERT, UPDATE, DELETE
    DDL = "ddl"                 # CREATE, ALTER TABLE
    HARD_BLOCK = "hard_block"   # DROP DATABASE, DROP SCHEMA, unbounded DELETE on large table
    GRANT = "grant"             # GRANT/REVOKE
    OTHER = "other"


@dataclass
class SqlClassification:
    category: SqlCategory
    statement_type: str  # e.g., "DELETE", "INSERT", "DROP TABLE"
    has_where: bool = True  # For UPDATE/DELETE — false means unbounded
    target_tables: list[str] | None = None
    risk_reason: str = ""  # Why it's classified as this
    is_hard_block: bool = False


# Hard-block keywords — these require explicit user override
HARD_BLOCK_STATEMENTS = {
    "DROP DATABASE",
    "DROP SCHEMA",
    "TRUNCATE",  # truncate is hard — no rollback via transaction
}


# payp's DbType values don't match sqlglot's dialect identifiers 1:1.
# DbType.POSTGRESQL.value == "postgresql" but sqlglot only accepts
# "postgres". Normalize at the classifier boundary so callers can pass
# the raw DbType.value without worrying about it.
_DIALECT_ALIASES = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "pg": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "oracle": "oracle",
    "oracledb": "oracle",
    "mongodb": "mongodb",
}


def _normalize_dialect(dialect: str) -> str:
    return _DIALECT_ALIASES.get((dialect or "").lower(), "postgres")


def _classify_mql(mql: str) -> SqlClassification:
    """Classify a MongoDB JSON-envelope query for security routing."""
    import json
    try:
        doc = json.loads(mql)
    except Exception:
        return SqlClassification(
            category=SqlCategory.OTHER,
            statement_type="UNPARSEABLE",
            risk_reason="Not valid JSON MQL envelope",
        )
    op = doc.get("op", "").lower()
    # Internal introspection ops - always safe
    if op in ("listcollections", "serverinfo", "dbstats", "collstats", "indexes", "sample"):
        return SqlClassification(category=SqlCategory.SELECT, statement_type=op.upper())
    # Read ops
    if op in ("find", "aggregate", "countdocuments", "distinct"):
        return SqlClassification(category=SqlCategory.SELECT, statement_type=op.upper())
    # Write ops
    if op in ("insertmany", "insertone"):
        return SqlClassification(category=SqlCategory.DML_WRITE, statement_type="INSERT")
    if op in ("updatemany", "updateone", "replaceone"):
        return SqlClassification(category=SqlCategory.DML_WRITE, statement_type="UPDATE")
    if op in ("deletemany", "deleteone"):
        has_filter = bool(doc.get("filter"))
        return SqlClassification(
            category=SqlCategory.DML_WRITE,
            statement_type="DELETE",
            has_where=has_filter,
            risk_reason="" if has_filter else "deleteMany with empty filter - deletes all documents",
        )
    # DDL-like
    if op in ("createindex", "dropindex"):
        return SqlClassification(category=SqlCategory.DDL, statement_type=op.upper())
    # Dangerous
    if op == "dropcollection":
        return SqlClassification(
            category=SqlCategory.HARD_BLOCK,
            statement_type="DROP COLLECTION",
            is_hard_block=True,
            risk_reason="Drops entire collection and all its data",
        )
    return SqlClassification(category=SqlCategory.OTHER, statement_type=op.upper())


def classify_sql(sql: str, dialect: str = "postgres") -> SqlClassification:
    """Classify a SQL statement for security mode routing.

    Returns SqlClassification with category and risk details.
    """
    dialect = _normalize_dialect(dialect)
    sql_stripped = sql.strip().rstrip(";").strip()
    if not sql_stripped:
        return SqlClassification(
            category=SqlCategory.OTHER,
            statement_type="EMPTY",
            risk_reason="Empty statement",
        )

    # MongoDB: bypass sqlglot entirely - parse JSON envelope
    if dialect == "mongodb":
        return _classify_mql(sql_stripped)

    try:
        # Suppress sqlglot warnings for "unsupported syntax" — those go to logger
        import logging
        logging.getLogger("sqlglot").setLevel(logging.ERROR)
        parsed = sqlglot.parse_one(sql_stripped, dialect=dialect)
    except Exception:
        # Can't parse — treat as risky
        return SqlClassification(
            category=SqlCategory.OTHER,
            statement_type="UNPARSEABLE",
            risk_reason="Could not parse SQL",
        )

    if parsed is None:
        return SqlClassification(
            category=SqlCategory.OTHER,
            statement_type="EMPTY",
        )

    # Extract target tables
    tables = [t.name for t in parsed.find_all(exp.Table)]

    # SELECT
    if isinstance(parsed, exp.Select):
        return SqlClassification(
            category=SqlCategory.SELECT,
            statement_type="SELECT",
            target_tables=tables,
        )

    # DROP — check severity
    if isinstance(parsed, exp.Drop):
        kind = (parsed.args.get("kind") or "").upper()
        if kind in ("DATABASE", "SCHEMA"):
            return SqlClassification(
                category=SqlCategory.HARD_BLOCK,
                statement_type=f"DROP {kind}",
                target_tables=tables,
                risk_reason=f"DROP {kind} is irreversible and affects all contents",
                is_hard_block=True,
            )
        return SqlClassification(
            category=SqlCategory.DDL,
            statement_type=f"DROP {kind}",
            target_tables=tables,
            risk_reason=f"DROP {kind} removes schema objects",
        )

    # TRUNCATE — hard block
    # sqlglot represents TRUNCATE as Command
    upper_sql = sql_stripped.upper()
    if upper_sql.startswith("TRUNCATE"):
        return SqlClassification(
            category=SqlCategory.HARD_BLOCK,
            statement_type="TRUNCATE",
            target_tables=tables,
            risk_reason="TRUNCATE cannot be rolled back from snapshots easily",
            is_hard_block=True,
        )

    # DELETE
    if isinstance(parsed, exp.Delete):
        has_where = parsed.args.get("where") is not None
        risk = "" if has_where else "DELETE without WHERE affects all rows"
        return SqlClassification(
            category=SqlCategory.DML_WRITE,
            statement_type="DELETE",
            has_where=has_where,
            target_tables=tables,
            risk_reason=risk,
        )

    # UPDATE
    if isinstance(parsed, exp.Update):
        has_where = parsed.args.get("where") is not None
        risk = "" if has_where else "UPDATE without WHERE affects all rows"
        return SqlClassification(
            category=SqlCategory.DML_WRITE,
            statement_type="UPDATE",
            has_where=has_where,
            target_tables=tables,
            risk_reason=risk,
        )

    # INSERT
    if isinstance(parsed, exp.Insert):
        return SqlClassification(
            category=SqlCategory.DML_WRITE,
            statement_type="INSERT",
            target_tables=tables,
        )

    # DDL — CREATE / ALTER
    alter_cls = getattr(exp, "Alter", None) or getattr(exp, "AlterTable", None)
    ddl_types: tuple = (exp.Create,)
    if alter_cls:
        ddl_types = (exp.Create, alter_cls)
    if isinstance(parsed, ddl_types):
        return SqlClassification(
            category=SqlCategory.DDL,
            statement_type=parsed.key.upper() if hasattr(parsed, 'key') else "DDL",
            target_tables=tables,
        )

    # GRANT/REVOKE
    if upper_sql.startswith("GRANT") or upper_sql.startswith("REVOKE"):
        return SqlClassification(
            category=SqlCategory.GRANT,
            statement_type=upper_sql.split()[0],
            target_tables=tables,
        )

    # Fallback
    return SqlClassification(
        category=SqlCategory.OTHER,
        statement_type=parsed.key.upper() if hasattr(parsed, 'key') else "UNKNOWN",
        target_tables=tables,
    )


def needs_approval(classification: SqlClassification) -> bool:
    """Whether this SQL needs user approval in manual/secure modes."""
    return classification.category in (
        SqlCategory.DML_WRITE,
        SqlCategory.DDL,
        SqlCategory.HARD_BLOCK,
        SqlCategory.GRANT,
    )


def needs_review(classification: SqlClassification) -> bool:
    """Whether this SQL needs reviewer model check in secure/secure-auto modes."""
    return needs_approval(classification)


# ---------------------------------------------------------------------------
# Static HARD_BLOCK pre-filter
# ---------------------------------------------------------------------------
# These patterns are checked BEFORE the LLM reviewer. A poisoned or confused
# model cannot "approve" its way past them — they require an explicit user
# override. Matches the principle that safety-critical decisions should be
# deterministic, not probabilistic.

# Per-dialect system schemas/owners whose DDL is always hard-blocked.
_SYSTEM_SCHEMAS: dict[str, frozenset[str]] = {
    "postgres": frozenset({
        "pg_catalog", "information_schema", "pg_toast", "pg_temp",
    }),
    "postgresql": frozenset({
        "pg_catalog", "information_schema", "pg_toast", "pg_temp",
    }),
    "mysql": frozenset({
        "mysql", "information_schema", "performance_schema", "sys",
    }),
    "oracle": frozenset({
        "sys", "system", "ctxsys", "mdsys", "xdb", "outln",
        "dbsnmp", "wmsys", "appqossys", "audsys", "gsmadmin_internal",
    }),
}

# Dangerous admin/control functions and commands. Matched as whole words
# case-insensitively. Not statement-type-specific — these should never
# auto-execute regardless of wrapping (SELECT pg_terminate_backend(...)
# is technically a SELECT but kills other sessions).
_DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bpg_terminate_backend\s*\(", "pg_terminate_backend kills other sessions"),
    (r"\bpg_cancel_backend\s*\(", "pg_cancel_backend cancels other sessions"),
    (r"\bdbms_shutdown\b", "DBMS_SHUTDOWN terminates the Oracle instance"),
    (r"^\s*shutdown\b", "SHUTDOWN command terminates the database server"),
    (r"\bxp_cmdshell\b", "xp_cmdshell executes arbitrary shell commands"),
    (r"\bload_file\s*\(", "load_file reads arbitrary files from the DB server"),
    (r"\bcopy\s+.*\s+program\s+'", "COPY ... PROGRAM executes shell commands"),
)


def _table_refers_to_system_schema(
    parsed: exp.Expression, dialect: str
) -> str | None:
    """Return the offending schema name if any table in the AST is in a
    protected system schema, else None."""
    system = _SYSTEM_SCHEMAS.get(dialect.lower(), frozenset())
    if not system:
        return None
    for table in parsed.find_all(exp.Table):
        # sqlglot exposes schema via .db (second-level name)
        schema = (table.db or "").lower()
        if schema and schema in system:
            return schema
    return None


def statically_hard_blocked(sql: str, dialect: str = "postgres") -> str | None:
    """Return a human-readable reason if SQL matches a deterministic block
    pattern, else None. Matches layer-1 safety rules that must never rely
    on LLM judgement.
    """
    dialect = _normalize_dialect(dialect)
    sql_stripped = sql.strip().rstrip(";").strip()
    if not sql_stripped:
        return None

    # MongoDB: use MQL classifier for static analysis
    if dialect == "mongodb":
        import json
        try:
            doc = json.loads(sql_stripped)
        except Exception:
            return None
        op = doc.get("op", "").lower()
        if op == "dropcollection":
            return "dropCollection destroys the entire collection"
        if op == "deletemany" and not doc.get("filter"):
            return "deleteMany with empty filter deletes all documents"
        return None

    # 1. Dangerous functions / admin commands (regex - AST-agnostic)
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, sql_stripped, re.IGNORECASE | re.MULTILINE):
            return reason

    # 2. Parse the AST for structural checks
    try:
        import logging
        logging.getLogger("sqlglot").setLevel(logging.ERROR)
        parsed = sqlglot.parse_one(sql_stripped, dialect=dialect)
    except Exception:
        # Unparseable — classify_sql already handles this; leave to LLM
        return None
    if parsed is None:
        return None

    # 3. DROP TABLE / DROP VIEW / DROP INDEX on a system schema, or any
    #    DROP DATABASE / DROP SCHEMA.
    if isinstance(parsed, exp.Drop):
        kind = (parsed.args.get("kind") or "").upper()
        if kind in ("DATABASE", "SCHEMA"):
            return f"DROP {kind} is irreversible and affects all contents"
        if kind in ("TABLE", "VIEW", "INDEX", "TABLESPACE"):
            sys_schema = _table_refers_to_system_schema(parsed, dialect)
            if sys_schema:
                return f"DROP {kind} targets system schema '{sys_schema}'"
            # Plain DROP TABLE is destructive but not always catastrophic;
            # leave it to needs_approval() — do not hard-block here.

    # 4. TRUNCATE — already hard-blocked in classify_sql, keep in sync here
    if sql_stripped.upper().startswith("TRUNCATE"):
        sys_schema = _table_refers_to_system_schema(parsed, dialect)
        if sys_schema:
            return f"TRUNCATE targets system schema '{sys_schema}'"
        # General TRUNCATE stays with classify_sql (HARD_BLOCK already)

    # 5. Unbounded DELETE / UPDATE — no WHERE clause whatsoever.
    if isinstance(parsed, exp.Delete):
        if parsed.args.get("where") is None:
            return "DELETE without WHERE clause affects every row"
    if isinstance(parsed, exp.Update):
        if parsed.args.get("where") is None:
            return "UPDATE without WHERE clause affects every row"

    # 6. ALTER on system schema
    alter_cls = getattr(exp, "Alter", None) or getattr(exp, "AlterTable", None)
    if alter_cls and isinstance(parsed, alter_cls):
        sys_schema = _table_refers_to_system_schema(parsed, dialect)
        if sys_schema:
            return f"ALTER targets system schema '{sys_schema}'"

    # 7. GRANT/REVOKE ALL PRIVILEGES on broad scopes — text match is good
    #    enough; sqlglot's GRANT support varies by version.
    upper = sql_stripped.upper()
    if re.search(r"\b(GRANT|REVOKE)\s+ALL\b.*\bON\s+(ALL|DATABASE|SCHEMA)\b", upper):
        return "GRANT/REVOKE ALL on database/schema scope"

    return None
