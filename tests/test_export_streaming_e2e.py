"""End-to-end streaming export test.

Full path:
    OpenRouter LLM → tool-call → payp's ExportTool → stream_raw → Postgres → file

Requires:
  - Running Postgres container (docker compose up -d postgres)
  - OpenRouter API key configured in ~/.payp/models.toml
  - Network egress to openrouter.ai

Run directly:
    python tests/test_export_streaming_e2e.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
import tempfile
from pathlib import Path

from payp.core.llm import LLMClient
from payp.db.connection import ConnectionManager
from payp.models import ConnectionCredential, ConnectionProfile, DbType
from payp.tools.export import ExportTool

PROFILE = ConnectionProfile(
    name="pg-local",
    db_type=DbType.POSTGRESQL,
    host="localhost",
    port=5432,
    database="payp_test",
    username="payp",
)
CREDENTIAL = ConnectionCredential(password="payp_dev")


async def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="payp_e2e_stream_"))
    print(f"tmp dir: {tmpdir}")

    # ------------------------------------------------------------------
    # 1. Bring up DB connection
    # ------------------------------------------------------------------
    conn = ConnectionManager(PROFILE, CREDENTIAL)
    try:
        version = await conn.connect()
        print(f"pg connected: {version}")
    except Exception as e:
        print(f"FAIL: postgres not reachable: {e}")
        return 1

    # ------------------------------------------------------------------
    # 2. Bring up LLM client
    # ------------------------------------------------------------------
    try:
        llm = LLMClient()
        model = llm.get_executor_model()
        print(f"llm model: {model}")
    except Exception as e:
        print(f"FAIL: llm client init: {e}")
        await conn.disconnect()
        return 1

    # ------------------------------------------------------------------
    # 3. Sanity-check that streaming actually fires on a small query
    # ------------------------------------------------------------------
    batches_seen = 0
    rows_seen = 0
    try:
        async for batch in conn.stream_raw(
            "SELECT id, name, email FROM customers ORDER BY id LIMIT 25",
            batch_size=10,
        ):
            batches_seen += 1
            rows_seen += len(batch)
        print(f"stream_raw: {batches_seen} batches, {rows_seen} rows (live PG)")
        if rows_seen == 0:
            print("FAIL: stream_raw returned zero rows — seed missing?")
            await conn.disconnect()
            return 1
    except Exception as e:
        print(f"FAIL: stream_raw live PG: {e}")
        await conn.disconnect()
        return 1

    # ------------------------------------------------------------------
    # 4. Tool-call loop: LLM picks export_query, payp runs it
    # ------------------------------------------------------------------
    export_path = tmpdir / "llm_requested.csv"
    export_tool = ExportTool()
    tool_def = export_tool.to_definition()
    context = {"connection_manager": conn}

    user_prompt = (
        "Export all rows from the `customers` table to CSV at exactly this "
        f"path: {export_path}. Use the export_query tool. The SQL should be "
        "`SELECT * FROM customers ORDER BY id`. Call the tool once and stop."
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a data engineering assistant. Use tools to perform "
                "database operations. When asked to export data, call the "
                "export_query tool with the exact arguments specified."
            ),
        },
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await llm.chat(messages=messages, tools=[tool_def], stream=False)
    except Exception as e:
        print(f"FAIL: llm.chat: {e}")
        await conn.disconnect()
        return 1

    if not response.tool_calls:
        print(f"FAIL: LLM did not call any tool. Content: {response.content!r}")
        await conn.disconnect()
        return 1

    call = response.tool_calls[0]
    print(f"llm tool call: {call.name}({json.dumps(call.arguments)[:200]})")

    if call.name != "export_query":
        print(f"FAIL: LLM called {call.name} instead of export_query")
        await conn.disconnect()
        return 1

    # Force our expected path even if LLM drifted
    args = dict(call.arguments)
    args["path"] = str(export_path)
    args.setdefault("format", "csv")
    args.setdefault("sql", "SELECT * FROM customers ORDER BY id")

    result = await export_tool.call(args, context)
    print(f"export result: success={result.success} data={result.data}")

    if not result.success:
        print(f"FAIL: export_query call failed: {result.error}")
        await conn.disconnect()
        return 1

    if not export_path.exists():
        print(f"FAIL: export file not created at {export_path}")
        await conn.disconnect()
        return 1

    if export_path.with_suffix(".csv.partial").exists():
        print("FAIL: .partial file left behind after successful export")
        await conn.disconnect()
        return 1

    # Verify content
    with open(export_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"csv row count: {len(rows)}")
    if len(rows) == 0:
        print("FAIL: exported csv is empty")
        await conn.disconnect()
        return 1

    reported = result.data.get("rows")
    if reported != len(rows):
        print(f"FAIL: tool reported {reported}, csv has {len(rows)}")
        await conn.disconnect()
        return 1

    await conn.disconnect()
    print()
    print(f"✓ e2e streaming export via LLM tool-call: {len(rows)} rows written")
    print(f"  cost: ${response.cost:.6f}")
    print(f"  tokens: {response.input_tokens} in / {response.output_tokens} out")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
