"""Dialect-aware identifier validation and quoting.

Identifiers (table / column / schema names) cannot be passed via driver
paramstyle — they must be interpolated into SQL text. To stay injection-safe:

1. `validate_ident` rejects anything outside a narrow allowlist (word chars,
   `$`, `#`, `@`, hyphen, space). Quotes, semicolons, parentheses, and control
   chars are refused up front.
2. `quote_ident` then wraps the already-validated name in dialect quoting and
   escapes any embedded quote character per SQL rules.

Call sites can interpolate the returned string into f-string SQL with
confidence. Bandit's B608 warnings on those sites are false positives; annotate
them with `# nosec B608` referencing this module.
"""

from __future__ import annotations

import re

from payp.models import DbType

# Permit letters, digits, underscore (\w), plus a handful of characters that
# show up in legitimate schema and table names across PG/MySQL/Oracle:
#   $  (MySQL/Oracle system tables, e.g. v$session)
#   #  (Oracle temp tables, MySQL system)
#   @  (Oracle DB links)
#   .  (schema.table — callers should split first, but allow for safety)
#   -  (rare but legal inside double-quoted PG identifiers)
#   space (legal inside double-quoted identifiers across all three)
_SAFE_IDENT = re.compile(r"^[\w $#@.\-]+$", re.UNICODE)


def validate_ident(name: str, *, what: str = "identifier") -> str:
    """Return `name` unchanged if safe; raise ValueError otherwise."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"Invalid {what}: empty")
    if len(name) > 128:
        raise ValueError(f"Invalid {what}: too long ({len(name)} chars)")
    if not _SAFE_IDENT.match(name):
        raise ValueError(f"Invalid {what}: {name!r}")
    return name


def quote_ident(db_type: DbType, ident: str) -> str:
    """Validate and return the identifier wrapped in dialect quoting."""
    validate_ident(ident)
    if db_type == DbType.MYSQL:
        return f"`{ident.replace('`', '``')}`"
    # PostgreSQL and Oracle both use double-quote
    return '"' + ident.replace('"', '""') + '"'


def qualified(db_type: DbType, schema: str | None, table: str) -> str:
    """Return a fully-quoted `schema.table` (or just `table`)."""
    tbl = quote_ident(db_type, table)
    if schema:
        return f"{quote_ident(db_type, schema)}.{tbl}"
    return tbl


def split_and_validate_table(raw: str) -> tuple[str | None, str]:
    """Parse `schema.table` or `table`; validate each component separately."""
    if "." in raw:
        schema, table = raw.split(".", 1)
        validate_ident(schema, what="schema")
        validate_ident(table, what="table")
        return schema, table
    validate_ident(raw, what="table")
    return None, raw
