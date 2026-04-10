# Memory — How payp Remembers Your Databases

payp builds a persistent knowledge base about every database it touches. Over time, it learns what tables mean, which columns matter, and how your business logic works — so it stops re-discovering the same things every session.

This document explains how memory works, where it lives, and how teams share it.

---

## TL;DR

| Thing | Where | Scope |
|---|---|---|
| Your knowledge (runtime store) | `~/.payp/knowledge/` or `~/.payp/mempalace/` | **Global** — one store per user, keyed by connection |
| Team-shared knowledge | `./payp/knowledge/` (anywhere you run `/knowledge export`) | **Per-project**, committed to git |
| Backends | `native` (markdown) or `mempalace` (ChromaDB + vector search) | Switchable: `/memory switch <backend>` |

**Key principle:** Knowledge is bound to the *database*, not the *project folder*. If you know something about `test-pg.customers`, that fact is true whether you launched payp from `~/work/report-A` or `~/work/report-B`. So payp stores it globally.

---

## The Two Backends

payp has a pluggable memory backend system. Both solve the same problem — persisting knowledge about tables — but with different tradeoffs.

### `native` — plain markdown files

```
~/.payp/knowledge/
├── test-pg/
│   ├── tables/
│   │   ├── customers.md
│   │   ├── orders.md
│   │   └── products.md
│   ├── views/
│   ├── procedures/
│   └── _overview.md          ← DB-level notes
├── prod-oracle/
│   └── tables/
│       └── ...
├── glossary.md               ← cross-DB shared notes
└── schema-notes.md
```

Each table is a markdown file. Easy to read, edit by hand, diff, and search with `grep`. No extra dependencies.

**Search** is keyword-based (case-insensitive substring across `.md` files). Fast enough for hundreds of tables, gets slow past a few thousand.

**Use native if:** you want human-readable files, git-commit everything mentality, and your knowledge base is small-to-medium.

### `mempalace` — ChromaDB vector store

```
~/.payp/mempalace/
├── chroma.sqlite3            ← drawer content + embeddings metadata
├── knowledge_graph.sqlite3   ← temporal knowledge graph (optional)
└── <uuid>/
    ├── data_level0.bin       ← HNSW index (binary)
    ├── header.bin
    └── length.bin
```

Knowledge is stored as **drawers** inside a palace — each drawer is a chunk of text indexed by a vector embedding. Under the hood, payp uses these mappings:

| payp concept | mempalace concept |
|---|---|
| connection | wing |
| table | room |
| section (business_logic, etc.) | hall |
| content chunk | drawer |

**Search** is semantic: `search_knowledge("timezone handling")` finds drawers whose *meaning* matches, not just keyword hits. Results are ranked by cosine similarity.

**Use mempalace if:** you have lots of knowledge, you want to search by concept rather than keyword, or you'd rather ask "how do we track soft-deletes?" than remember exact phrases.

**Install:** `pip install mempalace chromadb` (optional dependency).

---

## Switching Backends

```
payp> /memory status
  backend: native
  entries: 5
  size:    9.6KB
  healthy: ✓

payp> /memory switch mempalace
  Migrate 5 entries from 'native' to 'mempalace'? [y/N] y
  ✓ Migrated 5 entries → mempalace
  ✓ Now using mempalace backend
```

The switch is persistent — it updates `~/.payp/config.toml`:

```toml
[memory]
backend = "mempalace"
mempalace_dir = "~/.payp/mempalace"
```

Migration reads every entry from the old backend (`list_all` + `read`) and writes it into the new one (`save`). Both backends implement the same `MemoryBackend` protocol, so the switch works in either direction.

**Only the active backend is read at runtime.** If you switch native → mempalace and say "no" to migration, your native `.md` files stay on disk but payp stops reading them. Switch back later and they're still there.

---

## How Knowledge Gets Created

Knowledge is always created through a **propose → confirm** flow. payp never silently writes to your memory.

### The flow

1. You ask payp something: `"show me the top orders by value"`
2. payp queries the DB, discovers something non-obvious (e.g., the `status` column uses `1`/`2`/`3` for pending/confirmed/paid, not the strings you'd expect)
3. payp calls its internal `propose_knowledge` tool with the discovery
4. You see an approval panel:

   ```
   ╭─ 📝 New knowledge for orders (test-pg) ──────────────╮
   │ Status Column Semantics                              │
   │                                                      │
   │ The status column uses integer codes:                │
   │  • 1 = pending                                       │
   │  • 2 = confirmed                                     │
   │  • 3 = paid                                          │
   │                                                      │
   │ This is NOT a string enum despite field name looking │
   │ like one in the ORM.                                 │
   ╰─────────────── section: business_logic ──────────────╯
   Save to knowledge base? [y/N/e(edit)]:
   ```

5. You press `y` → saved. `e` → edit in `$EDITOR` first. `N` → discarded.

### What gets proposed

The LLM is instructed to propose knowledge when it discovers:

- **Column meanings** — what `status_code = 3` actually means
- **Enum values** — not documented in the schema but found in the data
- **NULL semantics** — when NULL means "missing" vs "not applicable" vs "not yet set"
- **Business rules** — "orders with status 3 are never amended"
- **Data quality quirks** — "this table has 2k duplicated rows from the 2024 import"
- **Valid filter patterns** — "always filter out soft-deleted rows with `deleted_at IS NULL`"
- **Relationship gotchas** — "this FK is declared but not enforced"

### How knowledge gets *used*

Before answering any question that touches a table, payp:

1. Calls `read_knowledge(table)` on the active backend
2. Injects that content into the LLM's context window alongside the schema DDL (T2 schema injection, see `docs/schema-context.md`)
3. Uses it to guide SQL generation and avoid re-discovery

You'll see these reads happen in the stream, prefixed with `→ read_knowledge(...)`.

---

## The `/knowledge` Command

```
/knowledge                             browse entries interactively
/knowledge export [conn] [path]        dump to markdown files
/knowledge import [path] [conn]        load markdown files into backend
/knowledge migrate-legacy              move ./payp/knowledge/ → ~/.payp/knowledge/
```

### Browse

```
payp> /knowledge
 Knowledge Base (3 entries) — backend: mempalace

 ▸ test-pg/customers        1 drawer
   test-pg/orders           2 drawers
   test-pg/products         1 drawer
```

- `↑↓` navigate
- `Enter` view the full markdown rendering
- `d` delete the selected entry
- `q`/`Esc` close

Header shows which backend is active, so you know which store you're browsing.

### Export (team sharing)

Export dumps the active backend's knowledge as plain markdown files. Works with any backend — for mempalace, drawers are concatenated per table and rendered to markdown.

```bash
# Export everything to ./payp/knowledge/
payp> /knowledge export

# Export only a specific connection
payp> /knowledge export test-pg

# Export to a custom path
payp> /knowledge export test-pg ~/shared/db-docs
```

Output structure:
```
./payp/knowledge/
└── test-pg/
    └── tables/
        ├── customers.md
        ├── orders.md
        └── products.md
```

### Import

The inverse of export. Reads `.md` files from a directory and saves them to the active backend.

```bash
# Import from the project-local share folder
payp> /knowledge import

# Import from a custom path
payp> /knowledge import ~/shared/db-docs

# Import only one connection from a shared folder
payp> /knowledge import ./payp/knowledge test-pg
```

Both operations use the same `MemoryBackend.save()` / `read()` APIs, so export/import works *across backends*:

- Alice uses native → exports to markdown → commits
- Bob pulls → runs `/knowledge import` with his mempalace backend active → the markdown becomes vector drawers

Markdown is the interchange format. Your runtime backend is a personal choice.

---

## Team Sharing Workflow

Knowledge sharing is **deliberate**, not automatic. payp never writes to your project folder at runtime — only when you explicitly run `/knowledge export`.

### Alice (the first person to learn something)

```bash
cd ~/work/sales-report
payp
# ... productive session, payp learns about order status codes, customer tiers, etc.
# ... Alice approves several propose_knowledge prompts ...

payp> /knowledge export test-pg
✓ Exported 3 table(s) from mempalace → /Users/alice/work/sales-report/payp/knowledge

# Alice commits the exported markdown
$ git add payp/knowledge
$ git commit -m "share db knowledge: orders, customers, products"
$ git push
```

Before committing, remember to unignore the path in your `.gitignore`:

```gitignore
# payp local state — generally ignored
payp/
# ...but commit shared knowledge exports
!payp/knowledge/
```

### Bob (the second person to benefit)

```bash
cd ~/work/sales-report
git pull

payp
payp> /knowledge import
✓ Imported 3 table(s) into native
```

Bob's next query against `customers` will have Alice's knowledge in its context. Without ever needing to ask her.

### Why not auto-write to the project folder?

Because it creates two sources of truth. If payp wrote to `./payp/knowledge/` *and* `~/.payp/knowledge/`, which one wins on conflict? What happens if Bob modifies the shared file but his backend has a different version?

The export/import model treats `./payp/knowledge/` as a **snapshot artifact** — a frozen export at a point in time, just like a compiled binary or a generated SDK. The runtime store is your personal working copy; the committed folder is what gets shared.

---

## Legacy Migration

If you used payp before this refactor, your old knowledge lives at `./payp/knowledge/` (project-local). The first time you start payp after upgrading, you'll see:

```
⚠ Found legacy ./payp/knowledge/ — knowledge is now global.
  Run /knowledge migrate-legacy to move it.
```

Then:

```
payp> /knowledge migrate-legacy
✓ Moved 5 file(s) from payp/knowledge → /Users/az/.payp/knowledge
Safe to rm -rf ./payp/knowledge now if you want.
```

The migration is non-destructive — the legacy dir is copied, not moved. If a file already exists in the global location, the legacy content is appended with a `<!-- merged from legacy -->` marker so nothing is overwritten silently.

---

## Architecture Reference

### MemoryBackend Protocol

Every backend implements this interface (`src/payp/memory/interface.py`):

```python
class MemoryBackend(Protocol):
    name: str

    async def read(self, connection: str, table: str) -> str | None: ...
    async def save(self, connection: str, table: str, content: str,
                   section: str = "business_logic") -> dict: ...
    async def search(self, query: str, connection: str | None = None,
                     limit: int = 5) -> list[dict]: ...
    async def list_all(self, connection: str | None = None) -> list[dict]: ...
    async def load_for_context(self, connection: str,
                               table_names: list[str]) -> str: ...
    async def delete(self, connection: str, table: str) -> bool: ...
    async def migrate_from(self, other: MemoryBackend,
                           connection: str | None = None) -> dict: ...
    def status(self) -> dict: ...
```

Adding a new backend (e.g., Postgres-backed, Redis-backed, remote API) means implementing these 8 methods and registering it in `memory/manager.py`. The LLM tools, `/knowledge` command, export/import, and context injection all work unchanged.

### Tools the LLM can call

| Tool | Purpose | Writes? |
|---|---|---|
| `read_knowledge(table)` | Load knowledge for a table before querying it | no |
| `search_knowledge(query)` | Find knowledge across all tables by keyword or concept | no |
| `list_knowledge()` | Enumerate all entries | no |
| `propose_knowledge(table, discovery, section)` | Request user approval to save new knowledge | no (until user confirms) |

**The LLM cannot write directly.** `write_knowledge` and `append_knowledge` tools exist in the codebase but are not registered in the LLM-facing tool registry. All writes must go through `propose_knowledge` → user confirmation → save.

### Files

```
src/payp/memory/
├── interface.py          ← MemoryBackend Protocol
├── manager.py            ← get_memory_backend, switch_backend, export/import
├── native.py             ← NativeMemoryBackend (markdown files)
└── mempalace_bridge.py   ← MemPalaceBackend (ChromaDB)

src/payp/storage/
└── knowledge.py          ← low-level .md file helpers (used by native backend)

src/payp/tools/
└── knowledge.py          ← LLM-facing tools (read, search, list, propose)

src/payp/core/
└── context.py            ← T2 schema injection + knowledge auto-load

src/payp/cli.py
└── _cmd_knowledge        ← /knowledge browse / export / import / migrate-legacy
└── _cmd_memory           ← /memory status / switch / migrate
```

---

## FAQ

**Q: Can I edit the .md files directly?**
Yes, if you're on the native backend. Just edit `~/.payp/knowledge/{conn}/tables/{table}.md` in any editor and the changes take effect immediately. On mempalace, edit via `/knowledge export → edit → /knowledge import`.

**Q: Does mempalace work offline?**
Yes. ChromaDB is fully local — no network calls. Embeddings are computed with a local default model.

**Q: What if two teammates have conflicting knowledge?**
Whoever imports last wins per table. If both have useful context, merge the markdown files by hand before importing. A future version may add a 3-way merge helper.

**Q: Can I share knowledge across different database engines?**
Knowledge is keyed by connection name, not engine. If Alice and Bob both have a connection named `test-pg` pointing to different databases, their knowledge will collide. Use distinct connection names (`alice-pg`, `bob-pg`) or namespace by team.

**Q: How much does knowledge cost in tokens?**
Only knowledge for tables *mentioned in the current query* is injected (via `build_t2_context`). Budget is ~30KB per turn. You can see what's loaded with `/context`.

**Q: Can I disable auto-knowledge proposals?**
Not yet via config, but you can simply say "no, don't save that" when prompted — the discovery is discarded. A future `[memory] auto_propose = false` setting will disable the whole prompt flow if you prefer to write knowledge by hand.
