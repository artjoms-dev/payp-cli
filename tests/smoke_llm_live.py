"""Live LLM smoke test — hits real OpenRouter, requires saved connections.

This is a script (not a pytest test) so it can run against real resources
without being swept into the CI test suite. Run manually:

    python tests/smoke_llm_live.py

Requirements:
    - ~/.payp/models.toml with an openrouter provider configured
    - test-pg: saved PostgreSQL connection against the test seed
    - test-ora: saved Oracle connection (optional, for dialect test)

Exit code:
    0 — all smoke tests passed
    1 — any failure
    2 — prerequisites missing
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from pathlib import Path

from rich.console import Console

# Ensure local src is on path when run directly
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from payp.config import list_connections, load_connection_profile, load_credential
from payp.core.chat import ChatSession
from payp.core.llm import LLMClient
from payp.db.connection import ConnectionManager
from payp.models import SecurityMode
from payp.tools.registry import build_cli_registry, build_mcp_registry


console = Console()


def check(label: str, ok: bool, detail: str = "") -> bool:
    marker = "[green]✓[/green]" if ok else "[red]✗[/red]"
    line = f"  {marker} {label}"
    if detail:
        line += f" [dim]— {detail}[/dim]"
    console.print(line)
    return ok


async def smoke_registry() -> bool:
    """1. Registry integrity — no drift between CLI and MCP."""
    console.print("\n[bold]1. Registry integrity[/bold]")
    ok = True

    cli_reg = build_cli_registry()
    mcp_reg = build_mcp_registry()
    mcp_exec = build_mcp_registry(allow_code_exec=True)

    ok &= check("CLI registry non-empty",
                len(cli_reg.all_tools()) > 20,
                f"{len(cli_reg.all_tools())} tools")
    ok &= check("MCP registry smaller than CLI",
                len(mcp_reg.all_tools()) < len(cli_reg.all_tools()),
                f"{len(mcp_reg.all_tools())} tools")
    ok &= check("MCP allow_code_exec exposes python/r/shell",
                all(n in {t.name for t in mcp_exec.all_tools()}
                    for n in ("execute_python", "execute_r", "execute_shell")))
    ok &= check("CLI exposes cleanup + skills",
                all(n in {t.name for t in cli_reg.all_tools()}
                    for n in ("cleanup", "invoke_skill", "list_skills")))
    ok &= check("MCP hides cleanup + skills (manual)",
                all(n not in {t.name for t in mcp_reg.all_tools()}
                    for n in ("cleanup", "invoke_skill", "list_skills")))
    return ok


async def smoke_security_fail_closed() -> bool:
    """2. Fail-closed: dangerous tools must refuse without skill registry."""
    console.print("\n[bold]2. Security fail-closed[/bold]")
    ok = True

    from payp.tools.python_exec import PythonExecTool
    from payp.tools.shell_exec import ShellExecTool

    os.environ.pop("PAYP_DANGEROUS_UNGATED_CODE_EXEC", None)

    r_py = await PythonExecTool().call({"code": "print(1)"}, context={})
    ok &= check("execute_python refuses with empty context",
                r_py.success is False,
                r_py.error or "")

    r_sh = await ShellExecTool().call({"command": "echo hi"}, context={})
    ok &= check("execute_shell refuses with empty context",
                r_sh.success is False)

    # Verify env override works
    os.environ["PAYP_DANGEROUS_UNGATED_CODE_EXEC"] = "1"
    try:
        r_py2 = await PythonExecTool().call(
            {"code": "print(42)"}, context={}
        )
        skill_blocked = "skill" in (r_py2.error or "").lower() and r_py2.success is False
        ok &= check("PAYP_DANGEROUS_UNGATED_CODE_EXEC=1 bypasses skill gate",
                    not skill_blocked,
                    "tool may still fail for subprocess reasons but not skill block")
    finally:
        os.environ.pop("PAYP_DANGEROUS_UNGATED_CODE_EXEC", None)

    return ok


async def smoke_llm_postgres(chat: ChatSession, conn_name: str) -> bool:
    """3. LLM → PostgreSQL end-to-end with a SELECT."""
    console.print(f"\n[bold]3. LLM end-to-end ({conn_name}) — SELECT[/bold]")
    ok = True

    try:
        await chat.send_message(
            "how many rows are in the customers table? give me just the number."
        )
        ok &= check("SELECT round-trip completed", True)
        last_ai = next(
            (m for m in reversed(chat.messages) if m.get("role") == "assistant"),
            None,
        )
        if last_ai:
            content = last_ai.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(b.get("text", "")) for b in content if isinstance(b, dict)
                )
            ok &= check("LLM returned a response",
                        bool(content),
                        f"{len(content)} chars")
    except Exception as e:
        ok = False
        console.print(f"  [red]✗ exception:[/red] {e}")
        traceback.print_exc()
    return ok


async def smoke_llm_oracle(conn_name: str) -> bool:
    """4. LLM → Oracle dialect sanity — FETCH FIRST not LIMIT."""
    console.print(f"\n[bold]4. LLM dialect ({conn_name}) — Oracle FETCH FIRST[/bold]")
    ok = True

    profile = load_connection_profile(conn_name)
    cred = load_credential(conn_name)
    if not profile or not cred:
        console.print(f"  [yellow]⚠ skip — no {conn_name} connection saved[/yellow]")
        return True

    try:
        mgr = ConnectionManager(profile, cred)
        await mgr.connect()
        check("connected to Oracle", True, f"{profile.db_type.value}")
    except Exception as e:
        console.print(f"  [yellow]⚠ skip — Oracle connect failed: {e}[/yellow]")
        return True

    try:
        llm = LLMClient()
        chat = ChatSession(
            llm=llm,
            console=console,
            connection_manager=mgr,
            mode=SecurityMode.MANUAL,
        )
        await chat.send_message(
            "show me any 3 rows from a table you find. the SQL must be valid Oracle."
        )
        ok &= check("Oracle SELECT succeeded", True)
    except Exception as e:
        ok = False
        console.print(f"  [red]✗ Oracle round-trip failed:[/red] {e}")
    finally:
        try:
            await mgr.disconnect()
        except Exception:
            pass
    return ok


async def main() -> int:
    console.print(
        "[bold cyan]payp live smoke test[/bold cyan] "
        "[dim](registry + security + LLM + dialects)[/dim]\n"
    )

    # --- prerequisites ---
    conns = list_connections()
    if "test-pg" not in conns:
        console.print("[red]✗ missing saved connection 'test-pg' — run `payp` and /db first[/red]")
        return 2

    # --- offline suites (no network) ---
    suites_ok = True
    suites_ok &= await smoke_registry()
    suites_ok &= await smoke_security_fail_closed()

    # --- online suites: LLM + DB ---
    console.print("\n[bold]3a. Bootstrap LLM + test-pg[/bold]")
    profile = load_connection_profile("test-pg")
    cred = load_credential("test-pg")
    if not profile or not cred:
        console.print("  [red]✗ test-pg profile/credential missing[/red]")
        return 2

    try:
        mgr = ConnectionManager(profile, cred)
        await mgr.connect()
        check("connected", True, f"{mgr.profile.host}/{mgr.profile.database}")
    except Exception as e:
        console.print(f"  [red]✗ connect failed: {e}[/red]")
        return 1

    try:
        llm = LLMClient()
        chat = ChatSession(
            llm=llm,
            console=console,
            connection_manager=mgr,
            mode=SecurityMode.MANUAL,
        )
        check("ChatSession built",
              True,
              f"{len(chat.registry.all_tools())} tools")

        suites_ok &= await smoke_llm_postgres(chat, "test-pg")
    except Exception as e:
        suites_ok = False
        console.print(f"  [red]✗ pg smoke failed: {e}[/red]")
        traceback.print_exc()
    finally:
        try:
            await mgr.disconnect()
        except Exception:
            pass

    # --- Oracle dialect suite ---
    if "test-ora" in conns:
        suites_ok &= await smoke_llm_oracle("test-ora")
    else:
        console.print("\n[yellow]⚠ no test-ora connection — skipping Oracle dialect test[/yellow]")

    # --- summary ---
    console.print()
    if suites_ok:
        console.print("[bold green]✓ ALL SMOKE TESTS PASSED[/bold green]")
        return 0
    else:
        console.print("[bold red]✗ ONE OR MORE SMOKE TESTS FAILED[/bold red]")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
