"""Single source of truth for the payp tool catalog.

Both the interactive CLI (chat.py) and the MCP server (mcp/tools_adapter.py)
build their tool registries from build_base_registry(). Passing an `exclude`
set produces surface-specific views without duplicating registrations.
"""

from __future__ import annotations

from payp.tools.base import ToolRegistry
from payp.tools.bulk_insert import BulkInsertTool
from payp.tools.chart import ChartTool
from payp.tools.cleanup import CleanupTool
from payp.tools.crossdb import (
    CompareDataTool,
    CompareSchemasTool,
    ListConnectionsTool,
    SwitchConnectionTool,
)
from payp.tools.dashboard import DashboardTool
from payp.tools.explain import ExplainTool
from payp.tools.export import ExportTool
from payp.tools.help_tool import PaypHelpTool
from payp.tools.knowledge import (
    ListKnowledgeTool,
    ProposeKnowledgeTool,
    ReadKnowledgeTool,
    SearchKnowledgeTool,
    WriteKnowledgeTool,
)
from payp.tools.python_exec import PythonExecTool
from payp.tools.queries import (
    DeleteQueryTool,
    ListQueriesTool,
    LoadQueryTool,
    SaveQueryTool,
)
from payp.tools.query import QueryTool
from payp.tools.r_exec import RExecTool
from payp.tools.schema import (
    CheckCascadeTool,
    SchemaLookupTool,
    SchemaSearchTool,
)
from payp.tools.shell_exec import ShellExecTool
from payp.tools.skills_tool import InvokeSkillTool, ListSkillsTool
from payp.tools.snapshot import (
    DeleteSnapshotTool,
    ListSnapshotsTool,
    RestoreSnapshotTool,
    SnapshotBeforeDeleteTool,
)
from payp.tools.stats import StatsTool
from payp.tools.web_fetch import WebFetchTool

# ---------------------------------------------------------------------------
# Tool category sets — used to build per-surface exclude lists.
# ---------------------------------------------------------------------------

#: Tools that only make sense inside the interactive CLI UI.
#: Skills are a CLI-level concept (user toggles activation); MCP clients
#: have their own tool enablement story and don't need these.
CLI_ONLY_TOOLS: frozenset[str] = frozenset({
    "invoke_skill",
    "list_skills",
})

#: Tools that rely on human-in-the-loop approval via the chat.py
#: `_execute_destructive_with_approval` wrapper. The CLI shows a preview
#: panel and waits for y/N. MCP has no equivalent yet (elicitation comes
#: in Phase 2), so these are hidden by default over MCP.
REQUIRES_INTERACTIVE_APPROVAL: frozenset[str] = frozenset({
    "propose_knowledge",
    "cleanup",
})

#: Code execution tools — massive attack surface. Always hidden from MCP
#: unless the operator explicitly opts in via PAYP_MCP_ALLOW_CODE_EXEC=1
#: on the server process. Even then, the tools themselves fail-closed if
#: the skill registry is absent (see python_exec.py et al), requiring
#: a SECOND env var PAYP_DANGEROUS_UNGATED_CODE_EXEC=1 to actually run.
CODE_EXECUTION_TOOLS: frozenset[str] = frozenset({
    "execute_python",
    "execute_r",
    "execute_shell",
})


# ---------------------------------------------------------------------------
# Canonical tool list — ordered by category for predictable listing.
# ---------------------------------------------------------------------------

def _all_tool_factories() -> list:
    """Return the ordered list of tool constructors.

    ADD NEW TOOLS HERE. This is the single append point for the catalog;
    both CLI and MCP inherit automatically.
    """
    return [
        # Database
        QueryTool,
        ExplainTool,
        SchemaLookupTool,
        SchemaSearchTool,
        CheckCascadeTool,
        StatsTool,
        # Snapshots (pre-DML backups + rollback)
        SnapshotBeforeDeleteTool,
        RestoreSnapshotTool,
        ListSnapshotsTool,
        DeleteSnapshotTool,
        # Export / help
        ExportTool,
        PaypHelpTool,
        # Knowledge base
        ReadKnowledgeTool,
        ProposeKnowledgeTool,
        WriteKnowledgeTool,
        ListKnowledgeTool,
        SearchKnowledgeTool,
        # Query library
        SaveQueryTool,
        ListQueriesTool,
        LoadQueryTool,
        DeleteQueryTool,
        # Visualization / bulk I/O
        ChartTool,
        BulkInsertTool,
        DashboardTool,
        # Multi-connection
        ListConnectionsTool,
        SwitchConnectionTool,
        CompareSchemasTool,
        CompareDataTool,
        # Skills (CLI-only)
        InvokeSkillTool,
        ListSkillsTool,
        # Code execution
        PythonExecTool,
        RExecTool,
        ShellExecTool,
        # Networking
        WebFetchTool,
        # Cleanup
        CleanupTool,
    ]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_base_registry(
    exclude: frozenset[str] | set[str] | None = None,
) -> ToolRegistry:
    """Build a tool registry, optionally excluding tools by name.

    This is THE single registration point for all payp tools. Both
    `build_cli_registry()` and `build_mcp_registry()` delegate here.
    """
    reg = ToolRegistry()
    excluded = set(exclude or ())
    for factory in _all_tool_factories():
        tool = factory()
        if tool.name in excluded:
            continue
        reg.register(tool)
    return reg


def build_cli_registry() -> ToolRegistry:
    """Registry for the interactive CLI — all tools enabled.

    `write_knowledge` is excluded because the CLI uses the
    `propose_knowledge` → user approval → save flow. Direct writes would
    bypass the approval panel.
    """
    return build_base_registry(exclude=frozenset({"write_knowledge"}))


def build_mcp_registry(
    *,
    mode: str = "manual",
    allow_code_exec: bool = False,
) -> ToolRegistry:
    """Registry for the MCP stdio server.

    Args:
        mode: "manual" (default, read-heavy) or "yolo" (writes allowed).
            Currently informational — destructive-tool enforcement happens
            in server.py's `_enforce_tool_policy`, not here.
        allow_code_exec: Expose execute_python/r/shell. Requires explicit
            opt-in — these are a massive attack surface in a headless
            environment with no user approval. Gated a second time by
            PAYP_DANGEROUS_UNGATED_CODE_EXEC inside the tools themselves.

    write_knowledge IS exposed in MCP (no propose/approve flow exists
    there, so direct write is the only way to persist knowledge from a
    Claude Code / Cursor session).
    """
    exclude: set[str] = set(CLI_ONLY_TOOLS)
    exclude |= set(REQUIRES_INTERACTIVE_APPROVAL)

    if not allow_code_exec:
        exclude |= set(CODE_EXECUTION_TOOLS)

    return build_base_registry(exclude=frozenset(exclude))
