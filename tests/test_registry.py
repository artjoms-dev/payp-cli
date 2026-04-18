"""Unit tests for the shared tool registry.

The registry is the single source of truth for both CLI and MCP tool
catalogs. These tests pin the expected contents so drift is caught
immediately.
"""

from __future__ import annotations

import time

import pytest

from payp.tools.registry import (
    CLI_ONLY_TOOLS,
    CODE_EXECUTION_TOOLS,
    REQUIRES_INTERACTIVE_APPROVAL,
    build_base_registry,
    build_cli_registry,
    build_mcp_registry,
)


# Pin the expected total so adding/removing a tool without updating here
# fails loudly. Bump when intentionally changing the catalog.
# 28 after merging 5 knowledge tools → `knowledge` and 4 snapshot tools → `snapshots`.
EXPECTED_BASE_TOTAL = 28


def test_base_registry_has_all_tools():
    reg = build_base_registry()
    tools = reg.all_tools()
    assert len(tools) == EXPECTED_BASE_TOTAL, (
        f"Expected {EXPECTED_BASE_TOTAL} tools in base registry, got {len(tools)}. "
        f"If you added/removed a tool, update EXPECTED_BASE_TOTAL and test_registry.py."
    )


def test_no_duplicate_tool_names():
    """Two tools must never share a name — would silently overwrite."""
    reg = build_base_registry()
    names = [t.name for t in reg.all_tools()]
    assert len(names) == len(set(names)), (
        f"Duplicate tool names: {[n for n in names if names.count(n) > 1]}"
    )


def test_cli_registry_hides_write_knowledge():
    """CLI uses knowledge(action=propose) → approval → save. Direct
    action=write is disabled on the merged tool's enum."""
    reg = build_cli_registry()
    names = {t.name for t in reg.all_tools()}
    assert "knowledge" in names
    actions = reg.get("knowledge").get_parameters_schema()["properties"]["action"]["enum"]
    assert "write" not in actions
    assert {"read", "search", "list", "propose"}.issubset(actions)


def test_cli_registry_has_cleanup_and_skills():
    """CLI gets the full destructive/skill surface (gated via chat.py approval)."""
    reg = build_cli_registry()
    names = {t.name for t in reg.all_tools()}
    assert "cleanup" in names
    assert "invoke_skill" in names
    assert "list_skills" in names


def test_mcp_default_excludes_cli_only():
    reg = build_mcp_registry()
    names = {t.name for t in reg.all_tools()}
    for tool_name in CLI_ONLY_TOOLS:
        assert tool_name not in names, (
            f"CLI-only tool {tool_name!r} leaked into MCP manual mode"
        )


def test_mcp_default_excludes_approval_gated():
    reg = build_mcp_registry()
    names = {t.name for t in reg.all_tools()}
    for tool_name in REQUIRES_INTERACTIVE_APPROVAL:
        assert tool_name not in names, (
            f"Approval-gated tool {tool_name!r} leaked into MCP manual mode"
        )


def test_mcp_default_excludes_code_execution():
    reg = build_mcp_registry()
    names = {t.name for t in reg.all_tools()}
    for tool_name in CODE_EXECUTION_TOOLS:
        assert tool_name not in names, (
            f"Code execution tool {tool_name!r} leaked into MCP manual mode"
        )


def test_mcp_allow_code_exec_exposes_code_tools():
    """PAYP_MCP_ALLOW_CODE_EXEC=1 flips code exec tools on (they still
    fail-closed at call time without PAYP_DANGEROUS_UNGATED_CODE_EXEC)."""
    reg = build_mcp_registry(allow_code_exec=True)
    names = {t.name for t in reg.all_tools()}
    for tool_name in CODE_EXECUTION_TOOLS:
        assert tool_name in names, (
            f"Code tool {tool_name!r} missing from allow_code_exec=True registry"
        )


def test_mcp_exposes_write_knowledge_directly():
    """MCP has no propose/approve UI — knowledge(action=write) IS enabled
    on the merged tool's schema."""
    reg = build_mcp_registry()
    names = {t.name for t in reg.all_tools()}
    assert "knowledge" in names
    actions = reg.get("knowledge").get_parameters_schema()["properties"]["action"]["enum"]
    assert {"read", "search", "list", "propose", "write"}.issubset(actions)


def test_mcp_exposes_core_db_tools():
    reg = build_mcp_registry()
    names = {t.name for t in reg.all_tools()}
    for core in (
        "execute_sql",
        "explain_query",
        "schema_lookup",
        "schema_search",
        "check_cascade",
        "table_stats",
        "snapshots",
        "export_query",
        "compare_schemas",
        "compare_data",
    ):
        assert core in names, f"Core DB tool {core!r} missing from MCP"


def test_exclude_filter_respects_arbitrary_names():
    reg = build_base_registry(exclude={"execute_sql"})
    names = {t.name for t in reg.all_tools()}
    assert "execute_sql" not in names
    # Everything else still present
    assert "explain_query" in names
    assert "schema_lookup" in names


def test_append_knowledge_tool_is_deleted():
    """AppendKnowledgeTool was removed — confirm it's not in the catalog."""
    reg = build_base_registry()
    names = {t.name for t in reg.all_tools()}
    assert "append_knowledge" not in names


# --- Performance sanity ---


def test_registry_build_is_fast():
    """Building a registry 100 times should be well under 1 second.

    Tool instantiation is the bulk of the cost; keep it cheap so test
    fixtures and MCP cold-starts stay snappy.
    """
    t0 = time.perf_counter()
    for _ in range(100):
        build_cli_registry()
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, (
        f"100x build_cli_registry took {elapsed:.2f}s — investigate slow tool __init__"
    )
