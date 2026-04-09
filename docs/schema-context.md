# payp — Schema Context & Knowledge Base

## The Problem

Real production databases have thousands of tables. A 4000-table database can't fit full DDL into any LLM context window. But the LLM needs schema awareness to generate correct SQL.

## Solution: Tiered Schema Context

### Tier Overview

| Tier | Content | When loaded | Approx size (4000 tables) |
|------|---------|-------------|--------------------------|
| T0 — Index | Schema names + table counts | Always in LLM context | ~1 KB |
| T1 — Catalog | All table names grouped by schema | Always in LLM context | ~20 KB |
| T2 — Relevant | Full DDL for conversation-relevant tables | Injected per query | Variable (~2-10 KB per table) |
| T3 — Deep | Indexes, triggers, procedures, stats | On explicit `/schema` request | Variable |

### T0 — Index (always loaded)
```
Database: analytics (PostgreSQL 16.2)
Schemas:
  - public: 247 tables, 31 views
  - staging: 89 tables, 5 views
  - reporting: 156 tables, 42 views
  - archive: 1,508 tables, 0 views
  - etl: 12 tables, 3 views
Total: 2,012 tables, 81 views
```

### T1 — Catalog (always loaded)
```
Schema: public (247 tables)
  users, user_roles, user_preferences, user_sessions,
  orders, order_items, order_status_history,
  products, product_categories, product_variants,
  customers, customer_addresses, customer_segments,
  invoices, invoice_lines, payments,
  ...

Schema: staging (89 tables)
  stg_orders, stg_products, stg_customers,
  ...
```

Just names. No columns, no types. This is enough for the LLM to know WHAT EXISTS and ask for details when needed.

### T2 — Relevant (injected per query)
When user says "show me orders by region":

1. payp scans T1 for likely matches: `orders`, `customers`, `customer_addresses` (has region), `order_items`
2. Loads full DDL for matched tables:

```sql
-- Injected into LLM context for this query:
CREATE TABLE orders (
    id BIGINT PRIMARY KEY,
    customer_id BIGINT REFERENCES customers(id),
    status SMALLINT NOT NULL DEFAULT 1,
    total_amount NUMERIC(12,2),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE customers (
    id BIGINT PRIMARY KEY,
    name VARCHAR(255),
    region VARCHAR(50),
    segment_id INT REFERENCES customer_segments(id)
);
```

3. LLM now has exact column names, types, and relationships.

### T3 — Deep (on explicit request)
```
$ /schema orders --deep

Table: orders
  Columns: id (BIGINT PK), customer_id (BIGINT FK→customers), status (SMALLINT), ...
  Indexes:
    - idx_orders_customer_id (customer_id) — btree
    - idx_orders_created_at (created_at) — btree
    - idx_orders_status_created (status, created_at) — btree, partial WHERE status < 3
  Constraints:
    - chk_orders_status CHECK (status BETWEEN 1 AND 5)
    - fk_orders_customer FOREIGN KEY (customer_id) → customers(id) ON DELETE CASCADE
  Triggers:
    - trg_orders_updated_at — BEFORE UPDATE — sets updated_at
  Stats:
    - ~12.4M rows, 2.1 GB, last vacuum 6h ago
  Referenced by:
    - order_items.order_id → orders.id
    - order_status_history.order_id → orders.id
    - invoices.order_id → orders.id
```

## Table Matching Algorithm

When user mentions tables (explicitly or implicitly), payp resolves which T2 schemas to load:

### Step 1: Exact name match
"show me orders" → match `orders`

### Step 2: FK traversal (1 hop)
`orders.customer_id` → also load `customers`

### Step 3: Fuzzy name match
"show me user activity" → match `users`, `user_sessions` (fuzzy on "activity" → "sessions")

### Step 4: LLM-assisted (if above fails)
Send T1 catalog + user query to LLM: "Which tables are relevant for this query?"
LLM returns table list → load T2 for those.

### Context Budget
- Max T2 injection per query: ~30 KB (roughly 10-15 tables with full DDL)
- If more tables needed: LLM gets DDL for top-priority tables + names-only for the rest
- User can pin tables: `/pin orders customers products` → always included in T2

## Schema Cache

### Storage
```
~/.payp/cache/
├── prod-analytics_t0.json          # Index — tiny, always fresh
├── prod-analytics_t1.json          # Catalog — all table names
├── prod-analytics_t2/              # Per-table DDL cache
│   ├── public.orders.sql
│   ├── public.customers.sql
│   └── ...
└── prod-analytics_meta.json        # Last refresh timestamp, table count
```

### Freshness Check (on connect)
```python
# Pseudocode
cached_count = cache.meta["table_count"]  # e.g., 247
current_count = db.query("SELECT COUNT(*) FROM information_schema.tables WHERE ...")

if cached_count != current_count:
    print("⚠ Schema changed since last session (247 → 249 tables). Run /schema --refresh")
else:
    print("✓ Schema cache up to date (247 tables)")
```

Cache is NOT auto-refreshed. User runs `/schema --refresh` to update. This prevents surprise cost/time on connect.

### Introspection Queries

payp uses `information_schema` (standard SQL) where possible, falling back to DB-specific catalogs:

| Need | PostgreSQL | MySQL | Oracle |
|------|-----------|-------|--------|
| Table list | `information_schema.tables` | `information_schema.tables` | `all_tables` |
| Columns | `information_schema.columns` | `information_schema.columns` | `all_tab_columns` |
| FKs | `information_schema.key_column_usage` | `information_schema.key_column_usage` | `all_constraints` + `all_cons_columns` |
| Indexes | `pg_indexes` | `information_schema.statistics` | `all_indexes` + `all_ind_columns` |
| Procedures | `pg_proc` | `information_schema.routines` | `all_procedures` |
| Row counts | `pg_stat_user_tables` (estimate) | `information_schema.tables` (estimate) | `all_tables.num_rows` (estimate) |

## Business Context (Knowledge Base)

Schema DDL tells the LLM structure. But not meaning. Business context fills the gap.

### Schema Annotations (`./payp/knowledge/schema-notes.md`)
```markdown
## orders
- status: 1=pending, 2=confirmed, 3=shipped, 4=delivered, 5=cancelled
- total_amount: includes tax, in EUR
- Soft-deleted via status=5, rows are never physically deleted

## customers
- region: free-text, main values are EU-West, EU-East, NA, APAC
- segment_id: 1=free, 2=starter, 3=pro, 4=enterprise
- GDPR applies: anonymize after 3 years inactive
```

### Conventions (`./payp/knowledge/conventions.md`)
```markdown
- All PKs are BIGINT auto-increment
- All tables have created_at and updated_at timestamps
- Soft-delete pattern: deleted_at TIMESTAMPTZ (NULL = active)
- Naming: snake_case, singular (user not users — but legacy tables use plural)
- Schema prefixes: stg_ = staging, rpt_ = reporting, tmp_ = temporary
```

These files are loaded into LLM context alongside T2 schemas. They help the LLM understand domain semantics.

## Pinned Tables

User can pin frequently-used tables to always be in T2 context:

```
$ /pin orders customers products
> ✓ Pinned 3 tables. These will always be in context.

$ /pin
> Pinned tables:
>   orders, customers, products
> [Add] [Remove] [Clear]

$ /unpin products
> ✓ Unpinned products.
```

Pinned tables stored in `./payp/payp.toml`:
```toml
[schema]
pinned_tables = ["orders", "customers", "products"]
```
