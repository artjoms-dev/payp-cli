# Prompt Token Diet — 3 Cherry-Picked Wins

Background: commit `86c0340` (empty, message-only) flagged the system-prompt
token usage on each turn. After a short look at `../openclaude`, three
techniques stand out as the highest leverage for payp. Each one below is a
*plan*: a one-line rationale, a minimal code sketch, and the exact file +
line range to edit. No code changes yet.

Measure before & after with `count_tokens` on the assembled prompt so we can
put a number on each win.

---

## #1 — Section the system prompt + mark a cache boundary

**What:** Split the prompt into a **static prefix** (identity, capabilities,
tool-output isolation, security mode copy, dialect rules) and a **dynamic
suffix** (active connection line, schema context, knowledge). Wrap the
static prefix as a separate content block with Anthropic prompt-caching so
subsequent turns in the same session reuse it at ~10% of the input cost.

**Why it wins:** the static prefix is the single biggest chunk of the
prompt and it is byte-identical across every turn of a session. Today we
pay full input-token price for it on every request.

**Where to edit:**

1. `src/payp/prompts/system.py:389` — `build_system_prompt` currently returns
   `"\n".join(sections)`. Change the signature to return a **tuple**
   `(static_prefix: str, dynamic_suffix: str)` or a list of content blocks.
   Keep sections 1–3 (and the dialect-rules block once #3 is done) in the
   static bucket; everything that depends on live session state (active
   connection line, T0/T1/FK, knowledge, skills) goes in the dynamic bucket.

2. `src/payp/core/chat/_facade.py:228` — callers of `build_system_prompt`.
   Update to unpack the tuple and pass both parts down to the LLM layer.

3. `src/payp/core/llm.py:154-159` and `:216-221` — where we build `kwargs`
   for `litellm.acompletion`. Replace the single system message with a
   multi-block system message:

   ```python
   # llm.py — inside chat() / chat_stream()
   system_blocks = [
       {
           "type": "text",
           "text": static_prefix,
           "cache_control": {"type": "ephemeral"},
       },
       {"type": "text", "text": dynamic_suffix},
   ]
   messages = [
       {"role": "system", "content": system_blocks},
       *user_and_assistant_messages,
   ]
   ```

   Gate behind a provider check — prompt caching is an Anthropic feature;
   for non-Anthropic routes through OpenRouter, fall back to a plain
   string so we don't 400. `litellm` forwards `cache_control` through for
   `anthropic/*` and `openrouter/anthropic/*` models.

4. `src/payp/core/compaction.py` (wherever `count_tokens` is used to
   estimate prompt size) — subtract the cached prefix from the
   "effective" context used to decide when to compact. Cached tokens
   still count toward the context window but not toward cost.

**Acceptance:** on the 2nd user turn of a session, `usage.cache_read_tokens`
(reported by litellm for Anthropic routes) is > 0 and input-token cost
drops to roughly 10% of the first turn's baseline for the static portion.

---

## #2 — Collapse the Skills section to a pointer

**What:** Today we list every skill's name + description + `when_to_use`
inline in the system prompt. Replace the whole block with one line that
tells the model to call `list_skills` on demand.

**Why it wins:** skills grow over time. Each new skill ships a permanent
token tax on every turn of every session, even when the user never hits
that skill. Advertise them via a tool, not the prompt.

**Where to edit:**

- `src/payp/prompts/system.py:522-544` — the `if skills:` block that builds
  `skill_lines`. Replace with:

  ```python
  if skills:
      sections.append(
          "## Available Skills\n"
          "Pre-defined workflows exist for common database tasks "
          "(schema audits, migrations, ETL checks, …). "
          "Call the `list_skills` tool to see what's available and what "
          "each one is for; call `invoke_skill` to load a specific one. "
          "Do this whenever the user's request sounds procedural or "
          "resembles a recurring operation."
      )
  ```

- `src/payp/tools/skills_tool.py` — confirm `list_skills` exists and
  returns `[{name, description, when_to_use}, …]`. If only `invoke_skill`
  exists today, add `list_skills` as a sibling tool in the same file; the
  implementation is a one-liner over `SkillRegistry.all()`.

**Acceptance:** the system prompt shrinks by ~N × ~60 tokens (where N is
the skill count). Verify the model still discovers and invokes skills for
procedural requests by running the existing skill-trigger fixtures.

---

## #3 — Move dialect rules into the `execute_sql` tool description

**What:** The Oracle dialect block alone is ~800 tokens (MySQL and PG add
more). Today it's injected into the system prompt on every turn regardless
of whether the model is about to write SQL. Move it into the `description`
of the `execute_sql` tool so providers surface it only when the tool is
actually considered.

**Why it wins:** most turns don't end in SQL execution (explanations,
schema questions, small talk about a table). Dialect rules only matter
right before SQL is emitted — exactly when the model is inspecting the
tool schema. Tool descriptions are also part of the static prefix that
benefits from #1's cache.

**Where to edit:**

1. `src/payp/prompts/system.py:417-428` — delete the `dialect_rules`
   injection from `build_system_prompt`. Keep the short "Active
   Connection: …" line (connection name + version + dialect name), drop
   the full `_dialect_rules_for(db_type)` body. The dialect name alone
   is enough for general reasoning; the hard rules move to the tool.

2. `src/payp/prompts/system.py:297` — `_dialect_rules_for(db_type)` stays
   as-is, but export it so the tool can call it.

3. `src/payp/tools/query.py:13-19` — make `QueryTool.description` a
   `property` (or build it in `__init__`) so it can read the active
   connection's dialect at tool-definition time:

   ```python
   class QueryTool(BaseTool):
       name = "execute_sql"

       def __init__(self, db_type: str = "postgresql") -> None:
           from payp.prompts.system import _dialect_rules_for
           self._db_type = db_type
           self.description = (
               "Execute a SQL query against the connected database. "
               "Returns rows, columns, row count, and execution time.\n\n"
               f"### {db_type.upper()} SYNTAX RULES — MUST FOLLOW\n"
               f"{_dialect_rules_for(db_type)}"
           )
   ```

4. `src/payp/tools/registry.py` (wherever `QueryTool()` is instantiated)
   — pass the active `db_type` in. If tools are registered once at
   startup and the user switches DBs mid-session, re-register
   `execute_sql` on `switch_connection` so its description reflects the
   new dialect. The registry already rebuilds on connection changes via
   `_ensure_chat_session` — piggyback on that.

**Acceptance:** system prompt drops by ~700–900 tokens on Oracle
connections, ~300–500 on others. SQL correctness on the existing
dialect-smoke tests stays at 100% because the rules are now seen by the
model at the exact moment it decides to emit SQL.

---

## Suggested order

Do #2 first (1-file change, immediate win, no risk), then #3 (medium,
needs registry plumbing), then #1 last (biggest payoff but touches llm.py
and the compaction path — worth its own PR with before/after token
numbers).
