# payp — Export & File System

## Export Formats

| Format | Library | Use Case |
|--------|---------|----------|
| CSV | `csv` (stdlib) | Universal, simple data exchange |
| JSON | `json` (stdlib) | API-friendly, nested data |
| Parquet | `pyarrow` | Analytics, large datasets, columnar |
| Excel | `openpyxl` | Analysts, business users, reporting |

## Export Flow

### From Query Results
```
payp> show me monthly revenue for 2025

  │ month   │ revenue     │
  │ 2025-01 │ 1,245,000   │
  │ 2025-02 │ 1,389,000   │
  │ ...     │ ...         │
  Showing 12 rows.

payp> export this to excel

  ✓ Exported to ./exports/monthly_revenue_2025_20260403.xlsx (12 rows)
```

### Via Command
```
payp> /export csv
  ✓ Exported last result to ./exports/query_result_20260403_101500.csv

payp> /export parquet ./data/revenue.parquet
  ✓ Exported to ./data/revenue.parquet
```

### Large Exports (from limited results)
```
payp> show me all orders

  Showing 20 of ~12,400,000 rows.

payp> export all to parquet

  ⚠ Full export: ~12.4M rows. Estimated size: ~800 MB (Parquet).
  This will stream directly from database to file (not through memory).
  
  [Proceed] [Cancel]

  > Proceed
  Exporting... ████████████████████ 12,400,000 rows [2m 15s]
  ✓ Exported to ./exports/orders_full_20260403.parquet (12.4M rows, 780 MB)
```

## Default Export Location

```
./exports/                          # Default export directory
├── monthly_revenue_2025_20260403.xlsx
├── orders_full_20260403.parquet
└── query_result_20260403_101500.csv
```

- Default: `./exports/` in current working directory
- User can specify any path: `/export csv /home/artjoms/data/report.csv`
- LLM can also write to any path user asks: "save it to my desktop"

## File System Access

payp's LLM can read and write files beyond the `./payp/` directory, similar to Claude Code. This is needed for:
- Exporting to user-specified locations
- Reading SQL files user wants to execute
- Reading CSV/JSON for data import scenarios
- Writing migration files

### Security Rules
- File operations follow the same security mode as SQL:
  - **Manual**: show file path + preview before writing
  - **YOLO**: write immediately
  - **Secure/Secure-auto**: reviewer checks file operations too
- Never write to system directories
- Never read files that look like credentials (unless user explicitly asks)
- Warn if overwriting existing files

## Streaming Large Exports

For exports >100K rows, payp streams directly from DB cursor to file writer:

```python
# Pseudocode — never loads full dataset into memory
with db.cursor("export_cursor") as cur:
    cur.execute(query)
    with open(path, "w") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        while batch := cur.fetchmany(10000):
            writer.writerows(batch)
            progress.update(len(batch))
```

This allows exporting tens of millions of rows without memory issues.
