"""Scratch integration test for commits 149da9f + 9526642.

Covers:
- FK graph discovery on live Postgres
- hash_table_names drift detection
- save_fk_graph / load_fk_graph round-trip
- format_schema_graph_for_context output
- System prompt includes FK graph section
- project_knowledge_dir resolution (precedence vs global)
- delete_connection removes .toml + .cred
- clear_connection_cache removes all 4 cache suffixes
- /db del state cleanup logic (simulated)

Run: python tests/_integration_commits_9526_149d.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def banner(msg: str) -> None:
    print(f"\n{'=' * 60}\n  {msg}\n{'=' * 60}")


async def main() -> int:
    # Use an isolated PAYP_HOME so we don't clobber real user data
    tmp_home = Path(tempfile.mkdtemp(prefix="payp_it_"))
    os.environ["PAYP_HOME"] = str(tmp_home)
    print(f"isolated PAYP_HOME = {tmp_home}")

    failures: list[str] = []

    # Import after env var is set so config picks it up
    from payp.config import (
        connections_dir,
        delete_connection,
        load_connection_profile,
        project_knowledge_dir,
        save_connection_profile,
        save_credential,
    )
    from payp.db.cache import (
        cache_dir,
        clear_connection_cache,
        load_fk_graph,
        save_fk_graph,
        save_metadata,
        save_t0,
        save_t1,
    )
    from payp.db.connection import ConnectionManager
    from payp.db.introspection import (
        discover_fk_graph,
        discover_t0,
        discover_t1,
        format_schema_graph_for_context,
        hash_table_names,
    )
    from payp.models import (
        ConnectionCredential,
        ConnectionProfile,
        DbType,
        SchemaCatalog,
        SchemaGraph,
    )
    from payp.prompts.system import build_system_prompt

    banner("1. Connect to live Postgres")
    profile = ConnectionProfile(
        name="it-pg",
        db_type=DbType.POSTGRESQL,
        host="localhost",
        port=5432,
        database="payp_test",
        username="payp",
    )
    credential = ConnectionCredential(password="payp_dev")
    conn = ConnectionManager(profile, credential)
    try:
        version = await conn.connect()
        print(f"  ok: {version[:60]}")
    except Exception as e:
        print(f"  FAIL connect: {e}")
        return 1

    try:
        banner("2. Discover T0, T1, FK graph")
        t0 = await discover_t0(conn)
        t1 = await discover_t1(conn)
        fk_graph = await discover_fk_graph(conn)
        print(f"  t0 tables: {t0.total_tables}, t1 schemas: {len(t1.tables)}")
        print(f"  fk edges: {len(fk_graph.edges)}")

        # Seed has: orders.customer_id->customers, order_items.order_id->orders,
        # order_items.product_id->products, payments.order_id->orders = 4 FKs
        expected_edges = {
            ("public.orders", "customer_id", "public.customers", "id"),
            ("public.order_items", "order_id", "public.orders", "id"),
            ("public.order_items", "product_id", "public.products", "id"),
            ("public.payments", "order_id", "public.orders", "id"),
        }
        actual = {tuple(e) for e in fk_graph.edges}
        missing = expected_edges - actual
        if missing:
            failures.append(f"FK graph missing edges: {missing}")
            print(f"  FAIL missing: {missing}")
        else:
            print(f"  ok: all 4 expected FK edges present")
        if len(actual) != 4:
            print(f"  note: got {len(actual)} edges total (seed defines 4)")

        banner("3. hash_table_names stability + drift detection")
        h1 = hash_table_names(t1)
        h2 = hash_table_names(t1)
        if h1 != h2:
            failures.append("hash_table_names not deterministic")
        drifted = SchemaCatalog(tables={**t1.tables, "public": t1.tables["public"] + ["zzz_new"]})
        h3 = hash_table_names(drifted)
        if h3 == h1:
            failures.append("hash_table_names failed to detect drift")
        else:
            print(f"  ok: stable={h1[:12]}, drifted={h3[:12]}")

        banner("4. save/load FK graph round-trip")
        fk_graph.table_names_hash = h1
        save_fk_graph("it-pg", fk_graph)
        loaded = load_fk_graph("it-pg")
        if loaded is None:
            failures.append("load_fk_graph returned None")
        elif loaded.table_names_hash != h1:
            failures.append(f"hash mismatch after load: {loaded.table_names_hash} != {h1}")
        elif len(loaded.edges) != len(fk_graph.edges):
            failures.append(f"edge count mismatch: {len(loaded.edges)} != {len(fk_graph.edges)}")
        else:
            round_trip_ok = {tuple(e) for e in loaded.edges} == actual
            if not round_trip_ok:
                failures.append("edge content differs after round-trip")
            else:
                print(f"  ok: {len(loaded.edges)} edges round-tripped, hash preserved")

        banner("5. format_schema_graph_for_context output")
        text = format_schema_graph_for_context(fk_graph)
        print("  " + text.replace("\n", "\n  "))
        if "### Foreign Key Graph" not in text or "->" not in text:
            failures.append("format_schema_graph_for_context output malformed")
        if "public.orders.customer_id -> public.customers.id" not in text:
            failures.append("expected edge text not in format output")

        banner("6. build_system_prompt includes FK graph")
        save_t0("it-pg", t0)
        save_t1("it-pg", t1)
        prompt = build_system_prompt(
            mode="manual",
            db_type="postgresql",
            t0=t0,
            t1=t1,
            fk_graph=fk_graph,
        )
        if "Foreign Key Graph" not in prompt:
            failures.append("system prompt missing 'Foreign Key Graph' section")
        else:
            idx = prompt.find("Foreign Key Graph")
            print(f"  ok: FK graph section at char {idx}")

        banner("7. project_knowledge_dir precedence")
        proj = tmp_home / "proj"
        (proj / "payp" / "knowledge").mkdir(parents=True)
        (proj / "payp" / "knowledge" / "domain.md").write_text("# Domain\nproject-local knowledge wins")
        # Also create a global knowledge file with same name
        (tmp_home / "knowledge").mkdir(parents=True)
        (tmp_home / "knowledge" / "domain.md").write_text("# Domain\nglobal knowledge")

        cwd_before = Path.cwd()
        os.chdir(proj)
        try:
            pk = project_knowledge_dir()
            if pk is None or pk != proj / "payp" / "knowledge":
                failures.append(f"project_knowledge_dir resolution wrong: {pk}")
            else:
                print(f"  ok: project dir = {pk}")

            # build_system_prompt should pick project-local content
            prompt2 = build_system_prompt(
                mode="manual",
                db_type="postgresql",
                knowledge_dir=tmp_home / "knowledge",  # global
            )
            if "project-local knowledge wins" not in prompt2:
                failures.append("project-local knowledge did not override global")
            elif "global knowledge" in prompt2:
                failures.append("global knowledge leaked in despite same filename")
            else:
                print("  ok: project-local content included, global overridden")
        finally:
            os.chdir(cwd_before)

        banner("8. delete_connection removes profile + credential")
        save_connection_profile(profile)
        save_credential("it-pg", credential)
        cdir = connections_dir()
        if not (cdir / "it-pg.toml").exists():
            failures.append(".toml not written before delete")
        if not (cdir / "it-pg.cred").exists():
            failures.append(".cred not written before delete")
        delete_connection("it-pg")
        if (cdir / "it-pg.toml").exists():
            failures.append(".toml not removed")
        if (cdir / "it-pg.cred").exists():
            failures.append(".cred not removed")
        if load_connection_profile("it-pg") is not None:
            failures.append("load_connection_profile still returns profile after delete")
        else:
            print("  ok: profile + credential removed")

        banner("9. clear_connection_cache removes all 4 cache files")
        save_t0("it-pg", t0)
        save_t1("it-pg", t1)
        save_metadata("it-pg", {"version": "test"})
        save_fk_graph("it-pg", fk_graph)
        cd = cache_dir()
        present_before = [
            f.name for f in cd.glob("it-pg_*.json")
        ]
        print(f"  before: {sorted(present_before)}")
        clear_connection_cache("it-pg")
        remaining = list(cd.glob("it-pg_*.json"))
        if remaining:
            failures.append(f"clear_connection_cache left files: {remaining}")
        else:
            print("  ok: all 4 cache files removed")

        banner("10. simulate /db del active-connection state cleanup")
        # Reproduce the relevant block from _delete_connection without
        # running prompt_toolkit interactive path
        from payp.cli.state import _state

        _state["active_connection"] = "it-pg"
        _state["connection_manager"] = "fake-mgr"
        _state["t0"] = t0
        _state["t1"] = t1
        _state["fk_graph"] = fk_graph
        target_name = "it-pg"
        if _state.get("active_connection") == target_name:
            _state.pop("active_connection", None)
            _state.pop("connection_manager", None)
            _state.pop("t0", None)
            _state.pop("t1", None)
            _state.pop("fk_graph", None)
        leftover = [
            k for k in ("active_connection", "connection_manager", "t0", "t1", "fk_graph")
            if _state.get(k) is not None
        ]
        if leftover:
            failures.append(f"state not fully cleaned: {leftover}")
        else:
            print("  ok: all active-connection state keys cleared")

    finally:
        try:
            await conn.disconnect()
        except Exception:
            pass
        # Clean up isolated home
        shutil.rmtree(tmp_home, ignore_errors=True)

    banner("SUMMARY")
    if failures:
        print(f"  FAIL ({len(failures)}):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("  ALL PASS")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
