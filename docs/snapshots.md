# payp — DML Snapshots & Reverse-SQL

## Overview

Before executing any DML that modifies data (UPDATE, DELETE), payp snapshots the affected rows so that the operation can be reversed. DDL reverse-SQL is generated statically (no snapshot needed).

## How It Works

### DDL — Static Reverse (no snapshot)
```
User: add status column to orders

Generated:    ALTER TABLE orders ADD COLUMN status SMALLINT DEFAULT 1;
Reverse-SQL:  ALTER TABLE orders DROP COLUMN status;
```
No data snapshot needed — DDL reverse is deterministic.

### DML — Snapshot + Reverse

#### UPDATE
```
User: set all pending orders to cancelled

Step 1 — payp generates:
  UPDATE orders SET status = 5 WHERE status = 1;

Step 2 — before executing, payp runs count check:
  SELECT COUNT(*) FROM orders WHERE status = 1;
  → 847 rows

Step 3 — size check:
  847 rows → under limit (10,000 rows) → proceed with snapshot

Step 4 — snapshot:
  SELECT * FROM orders WHERE status = 1;
  → saved to ./payp/snapshots/2026-04-03_orders_update_a3f2.jsonl

Step 5 — execute UPDATE

Step 6 — reverse-SQL generated from snapshot:
  -- Reverse: restore 847 rows to status = 1
  UPDATE orders SET status = 1 WHERE id IN (12, 45, 78, ...);
  -- Full row data available in snapshot file for complete restore
```

#### DELETE
```
User: delete inactive users older than 2 years

Step 1 — payp generates:
  DELETE FROM users WHERE active = false AND last_login < '2024-04-03';

Step 2 — count check:
  SELECT COUNT(*) FROM users WHERE active = false AND last_login < '2024-04-03';
  → 2,341 rows

Step 3 — size check: 2,341 rows → under limit → snapshot

Step 4 — snapshot:
  SELECT * FROM users WHERE active = false AND last_login < '2024-04-03';
  → saved to ./payp/snapshots/2026-04-03_users_delete_b7c1.jsonl

Step 5 — execute DELETE

Step 6 — reverse-SQL:
  -- Reverse: re-insert 2,341 deleted rows
  INSERT INTO users (id, name, email, active, last_login, ...)
  VALUES (101, 'John Doe', 'john@...', false, '2023-01-15', ...),
         (102, ...),
         ...;
  -- Full data in snapshot: ./payp/snapshots/2026-04-03_users_delete_b7c1.jsonl
```

## Size Limits & User Decisions

### Thresholds
```toml
# ./payp/payp.toml or ~/.payp/config.toml
[snapshots]
max_rows = 10000          # snapshot up to this many rows
max_size_mb = 50          # snapshot up to this file size
warn_rows = 1000          # warn user above this count
retention_days = 30       # auto-delete snapshots older than this
```

### When Over Limit
```
User: delete all archived orders

payp checks:
  SELECT COUNT(*) FROM orders WHERE status = 5;
  → 485,000 rows

  ⚠ This operation affects 485,000 rows.
  Snapshot limit is 10,000 rows / 50 MB.
  
  Options:
  [1] Execute WITHOUT snapshot (no reverse available)
  [2] Export affected rows first, then execute
  [3] Execute in batches of 10,000 (with snapshots per batch)
  [4] Cancel

  Select: 
```

### When Near Limit
```
payp checks:
  → 3,200 rows (above warn threshold of 1,000)

  ℹ This operation affects 3,200 rows. Snapshot will be ~4.2 MB.
  [Execute with snapshot] [Execute without snapshot] [Cancel]
```

## Snapshot Storage

```
./payp/snapshots/
├── 2026-04-03_orders_update_a3f2.jsonl      # 847 rows, 1.2 MB
├── 2026-04-03_users_delete_b7c1.jsonl       # 2,341 rows, 3.8 MB
├── 2026-04-02_products_update_c4d5.jsonl    # 45 rows, 12 KB
└── manifest.json                             # index of all snapshots
```

### Snapshot File Format (JSONL)
```jsonl
{"_payp_meta": {"table": "orders", "operation": "UPDATE", "where": "status = 1", "timestamp": "2026-04-03T10:15:00Z", "connection": "prod-analytics", "row_count": 847}}
{"id": 12, "customer_id": 401, "status": 1, "total_amount": 150.00, "created_at": "2026-03-01T08:00:00Z"}
{"id": 45, "customer_id": 402, "status": 1, "total_amount": 89.99, "created_at": "2026-03-02T14:30:00Z"}
...
```

### manifest.json
```json
{
  "snapshots": [
    {
      "id": "a3f2",
      "file": "2026-04-03_orders_update_a3f2.jsonl",
      "table": "orders",
      "operation": "UPDATE",
      "rows": 847,
      "size_bytes": 1258000,
      "timestamp": "2026-04-03T10:15:00Z",
      "connection": "prod-analytics",
      "transaction_id": 42
    }
  ]
}
```

## Rollback Flow

```
$ /rollback
> Recent operations with snapshots:
>   1. [10m ago] UPDATE orders SET status=5 (847 rows) — snapshot ✓
>   2. [2h ago] DELETE users (2,341 rows) — snapshot ✓
>   3. [1d ago] UPDATE products SET price (45 rows) — snapshot ✓
>
> Select operation to reverse: 1
>
> Reverse SQL:
>   UPDATE orders SET status = 1 WHERE id IN (12, 45, 78, ...);
>   -- Restores 847 rows to original state
>
> [Execute reverse] [Show full SQL] [Export SQL file] [Cancel]
```

## Edge Cases

### Concurrent Changes
If someone else modified the snapshotted rows between the original operation and the rollback:
```
⚠ 12 of 847 rows have been modified since the snapshot.
Rollback will overwrite those changes.
[Proceed] [Show conflicts] [Cancel]
```

### Tables with Generated Columns / Triggers
Snapshot captures the state, but INSERT-back might trigger auto-increment conflicts or trigger side effects. payp warns:
```
⚠ Table 'users' has:
  - Auto-increment PK (id) — INSERT will use explicit IDs
  - Trigger: trg_users_audit — will fire on INSERT
[Proceed with explicit IDs] [Cancel]
```

### Binary / Large Columns (BLOB, BYTEA)
```toml
[snapshots]
skip_binary_columns = true    # default: skip BLOBs in snapshots
binary_warning = true         # warn user that binary data is not snapshotted
```
