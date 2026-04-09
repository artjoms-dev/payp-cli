"""SQL classifier — detect operation type and risk level.

Used by security modes to decide which flow to apply.
"""

from __future__ import annotations

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


def classify_sql(sql: str, dialect: str = "postgres") -> SqlClassification:
    """Classify a SQL statement for security mode routing.

    Returns SqlClassification with category and risk details.
    """
    sql_stripped = sql.strip().rstrip(";").strip()
    if not sql_stripped:
        return SqlClassification(
            category=SqlCategory.OTHER,
            statement_type="EMPTY",
            risk_reason="Empty statement",
        )

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
