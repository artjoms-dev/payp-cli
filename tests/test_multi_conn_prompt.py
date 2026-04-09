"""Tests for multi-connection awareness in the system prompt."""

from __future__ import annotations

from payp.models import SecurityMode
from payp.prompts.system import build_system_prompt


def test_single_connection_no_active_connections_section():
    """With a single connection, the 'Active Connections' list section should NOT appear."""
    active_connections = [
        {
            "name": "prod-pg",
            "db_type": "postgresql",
            "version": "16.2",
            "host": "localhost",
            "database": "app",
            "is_active": True,
            "is_connected": True,
            "table_count": 42,
        }
    ]

    prompt = build_system_prompt(
        mode=SecurityMode.MANUAL,
        connection_name="prod-pg",
        db_version="16.2",
        active_connections=active_connections,
    )

    # Should NOT contain the multi-DB list header
    assert "## Active Connections" not in prompt
    assert "## Dialect Syntax Guide" not in prompt
    # But should still have the single-conn "Active Connection" section
    assert "## Active Connection" in prompt
    assert "prod-pg" in prompt


def test_no_active_connections_arg_behaves_normally():
    """When active_connections is None, nothing new is added."""
    prompt = build_system_prompt(
        mode=SecurityMode.MANUAL,
        connection_name="prod-pg",
        db_version="16.2",
        active_connections=None,
    )
    assert "## Active Connections" not in prompt
    assert "## Dialect Syntax Guide" not in prompt


def test_three_connections_renders_full_section():
    """With 3 connections of different dialects, show the list and dialect guide."""
    active_connections = [
        {
            "name": "prod-pg",
            "db_type": "postgresql",
            "version": "16.2",
            "host": "db1",
            "database": "app",
            "is_active": True,
            "is_connected": True,
            "table_count": 42,
        },
        {
            "name": "staging-mysql",
            "db_type": "mysql",
            "version": "8.0",
            "host": "db2",
            "database": "stage",
            "is_active": False,
            "is_connected": True,
            "table_count": 12,
        },
        {
            "name": "oracle-dwh",
            "db_type": "oracle",
            "version": "23",
            "host": "db3",
            "database": "DWH",
            "is_active": False,
            "is_connected": True,
            "table_count": 500,
        },
    ]

    prompt = build_system_prompt(
        mode=SecurityMode.MANUAL,
        connection_name="prod-pg",
        db_version="16.2",
        active_connections=active_connections,
    )

    # Section headers
    assert "## Active Connections" in prompt
    assert "## Dialect Syntax Guide" in prompt

    # Summary line
    assert "3 active database connections" in prompt
    assert '"prod-pg"' in prompt  # default name quoted

    # All three connections listed
    assert "prod-pg" in prompt
    assert "staging-mysql" in prompt
    assert "oracle-dwh" in prompt

    # Versions / type labels rendered
    assert "PostgreSQL 16.2" in prompt
    assert "MySQL 8.0" in prompt
    assert "Oracle 23" in prompt

    # Default marker
    assert "← default" in prompt
    # Active vs inactive markers
    assert "●" in prompt
    assert "○" in prompt


def test_dialect_guide_contents():
    """The dialect guide must call out key syntax differences."""
    active_connections = [
        {
            "name": "prod-pg",
            "db_type": "postgresql",
            "version": "16.2",
            "host": "h",
            "database": "d",
            "is_active": True,
            "is_connected": True,
            "table_count": 0,
        },
        {
            "name": "staging-mysql",
            "db_type": "mysql",
            "version": "8.0",
            "host": "h",
            "database": "d",
            "is_active": False,
            "is_connected": True,
            "table_count": 0,
        },
        {
            "name": "oracle-dwh",
            "db_type": "oracle",
            "version": "23",
            "host": "h",
            "database": "d",
            "is_active": False,
            "is_connected": True,
            "table_count": 0,
        },
    ]

    prompt = build_system_prompt(
        mode=SecurityMode.MANUAL,
        connection_name="prod-pg",
        db_version="16.2",
        active_connections=active_connections,
    )

    # Key dialect syntax notes
    assert "FETCH FIRST" in prompt
    assert "LIMIT" in prompt
    assert "dual" in prompt.lower()
    assert "backtick" in prompt.lower()
    assert "UPPERCASE" in prompt  # Oracle
    assert "lowercase" in prompt  # Postgres

    # Routing guidance
    assert "@" in prompt  # @name prefix
    assert "switch_connection" in prompt
    assert "compare_schemas" in prompt
    assert "compare_data" in prompt


def test_two_connections_still_triggers_section():
    """Boundary: exactly 2 connections should also show the section (>1)."""
    active_connections = [
        {
            "name": "pg1",
            "db_type": "postgresql",
            "version": "15",
            "host": "h",
            "database": "d",
            "is_active": True,
            "is_connected": True,
            "table_count": 0,
        },
        {
            "name": "pg2",
            "db_type": "postgresql",
            "version": "16",
            "host": "h",
            "database": "d",
            "is_active": False,
            "is_connected": True,
            "table_count": 0,
        },
    ]
    prompt = build_system_prompt(
        mode=SecurityMode.MANUAL,
        connection_name="pg1",
        db_version="15",
        active_connections=active_connections,
    )
    assert "## Active Connections" in prompt
    assert "2 active database connections" in prompt
    # Only PostgreSQL dialect listed once, but both conn names included
    assert "pg1, pg2" in prompt or ("pg1" in prompt and "pg2" in prompt)
