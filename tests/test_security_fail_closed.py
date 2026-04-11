"""Security regression tests — dangerous tools must fail-closed.

These tools (python_exec, r_exec, shell_exec, web_fetch) are skill-gated
in CLI mode. When the skill registry is absent (e.g. MCP headless mode),
they MUST refuse to run unless an explicit env var opts in.

The previous implementation used `if skills is not None and ...`, which
silently bypassed the gate when skills was None. This test suite pins
the fail-closed behavior so the bug can't return.
"""

from __future__ import annotations

import pytest

from payp.tools.base import ToolResult
from payp.tools.python_exec import PythonExecTool
from payp.tools.r_exec import RExecTool
from payp.tools.shell_exec import ShellExecTool
from payp.tools.web_fetch import WebFetchTool


# Each entry: (ToolClass, arg_dict, skill_name_substring)
DANGEROUS_TOOLS = [
    (PythonExecTool, {"code": "print('hi')"}, "python"),
    (RExecTool, {"code": "print('hi')"}, "r"),
    (ShellExecTool, {"command": "echo hi"}, "shell"),
    (WebFetchTool, {"url": "https://example.com"}, "web"),
]


@pytest.mark.parametrize("tool_cls,args,_keyword", DANGEROUS_TOOLS)
async def test_tool_fails_closed_when_no_skills(tool_cls, args, _keyword, monkeypatch):
    """When context has no skill registry, the tool MUST refuse.

    This mirrors the MCP headless scenario. Without the override env
    var, the tool must return success=False.
    """
    monkeypatch.delenv("PAYP_DANGEROUS_UNGATED_CODE_EXEC", raising=False)
    tool = tool_cls()
    result: ToolResult = await tool.call(args, context={})
    assert result.success is False, (
        f"{tool_cls.__name__} failed OPEN when skill registry is missing — "
        f"this is the exact security bug the fail-closed fix prevents. "
        f"result={result}"
    )
    # Error message should mention skill registry or fail-closed
    msg = ((result.error or "") + (result.summary or "")).lower()
    assert "skill" in msg or "fail-closed" in msg, (
        f"Error message should explain why: {result.error!r} / {result.summary!r}"
    )


@pytest.mark.parametrize("tool_cls,args,_keyword", DANGEROUS_TOOLS)
async def test_tool_fails_closed_with_empty_skills_context(
    tool_cls, args, _keyword, monkeypatch
):
    """Same test, but context has skills=None explicitly (vs missing key)."""
    monkeypatch.delenv("PAYP_DANGEROUS_UNGATED_CODE_EXEC", raising=False)
    tool = tool_cls()
    result = await tool.call(args, context={"skills": None})
    assert result.success is False


async def test_env_override_lets_python_exec_proceed(monkeypatch):
    """PAYP_DANGEROUS_UNGATED_CODE_EXEC=1 bypasses the fail-closed for python_exec.

    The tool may still fail for environmental reasons (subprocess, Python
    not installed), but it must NOT fail with a skill-registry message.
    """
    monkeypatch.setenv("PAYP_DANGEROUS_UNGATED_CODE_EXEC", "1")
    tool = PythonExecTool()
    result = await tool.call({"code": "print(1+1)"}, context={})
    # Should have passed the fail-closed gate. Whether it actually
    # executed depends on the subprocess environment — we only check
    # that the block DIDN'T fire.
    err = (result.error or "").lower()
    summary = (result.summary or "").lower()
    assert "no skill registry" not in err + summary
    assert "fail-closed" not in err + summary


async def test_env_override_lets_shell_proceed(monkeypatch):
    monkeypatch.setenv("PAYP_DANGEROUS_UNGATED_CODE_EXEC", "1")
    tool = ShellExecTool()
    result = await tool.call({"command": "echo hi"}, context={})
    err = (result.error or "").lower()
    summary = (result.summary or "").lower()
    assert "no skill registry" not in err + summary
    assert "fail-closed" not in err + summary


class _FakeInactiveSkills:
    """Minimal stub — skill registry present, but the target is NOT active."""

    def is_active(self, name: str) -> bool:
        return False

    def active(self):
        return []


@pytest.mark.parametrize("tool_cls,args,_keyword", DANGEROUS_TOOLS)
async def test_tool_refuses_when_skill_present_but_inactive(
    tool_cls, args, _keyword
):
    """Regression for the OLD CLI gate path — still blocks when skill
    registry exists but the specific skill isn't activated by the user."""
    tool = tool_cls()
    result = await tool.call(args, context={"skills": _FakeInactiveSkills()})
    assert result.success is False
    msg = (result.error or "").lower()
    assert "skill" in msg


async def test_env_override_does_not_bypass_inactive_skill():
    """The override env var only kicks in when skills registry is missing.

    If a registry exists but the skill is inactive, the env var MUST NOT
    silently enable the tool — otherwise it becomes a backdoor around the
    user's CLI /skills activation.
    """
    import os
    os.environ["PAYP_DANGEROUS_UNGATED_CODE_EXEC"] = "1"
    try:
        tool = PythonExecTool()
        result = await tool.call(
            {"code": "print(1)"},
            context={"skills": _FakeInactiveSkills()},
        )
        assert result.success is False
        assert "skill" in (result.error or "").lower()
    finally:
        os.environ.pop("PAYP_DANGEROUS_UNGATED_CODE_EXEC", None)
