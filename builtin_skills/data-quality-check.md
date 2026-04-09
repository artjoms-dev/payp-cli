---
name: data-quality-check
description: Comprehensive audit of a table — nulls, duplicates, out-of-range dates, referential integrity, outliers
when_to_use: User asks to audit, validate, check quality of, or find bad data in a specific table
allowed_tools: [table_stats, schema_lookup, execute_sql, check_cascade]
db_types: [postgresql, mysql, oracle]
author: payp-team
version: 1.0
---

## Data Quality Check Workflow

A read-only audit of a single table. This skill **REPORTS** issues — it does NOT fix them. Never run UPDATE/DELETE/INSERT/ALTER as part of this skill.

Gather evidence first, then present a prioritized findings report at the end.

### Dialect notes

- **Oracle**: use `FETCH FIRST n ROWS ONLY` (not `LIMIT`), `SYSDATE` (not `NOW()`), `ADD_MONTHS(SYSDATE, -12)` for date math
- **PostgreSQL / MySQL**: use `NOW()`, `LIMIT n`, `NOW() - INTERVAL '1 year'`
- Use `COALESCE(col, default)` for NULL-safe comparisons
- Quote identifiers safely: `"{table}"` in PG, `` `{table}` `` in MySQL, `{TABLE}` in Oracle

---

### Step 1 — Schema fingerprint

Call `schema_lookup(table)` to get: column list, data types, primary key, foreign key constraints, NOT NULL flags, indexes.

Record:
- PK column(s)
- FK columns → target tables
- NOT NULL columns (these must NOT have nulls)
- Date/timestamp columns (for step 6)
- Numeric columns (for step 8)
- String/varchar columns (for step 9)

---

### Step 2 — Basic stats

Call `table_stats(table)`. This returns row count + per-column: null count, null %, distinct count, min, max.

Keep the result handy — steps 3, 8, 10 refer to it.

---

### Step 3 — Null analysis

From `table_stats`:

- **CRITICAL**: any NOT NULL column with null_count > 0 (schema violation, should be impossible)
- **WARNING**: nullable columns with null % > 20%
- **INFO**: nullable columns with null % between 5% and 20%

Fallback query if stats are unavailable:

```sql
SELECT
  COUNT(*) AS total_rows,
  SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) AS null_count,
  ROUND(100.0 * SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2) AS null_pct
FROM {table};
```

---

### Step 4 — Duplicate primary keys

PKs should NEVER duplicate. Any result > 0 rows is **CRITICAL**.

```sql
-- PG / MySQL / Oracle (single-column PK)
SELECT {pk}, COUNT(*) AS cnt
FROM {table}
GROUP BY {pk}
HAVING COUNT(*) > 1;
```

For composite PKs, group by all PK columns. Report the top 10 offending key values.

---

### Step 5 — Duplicate natural keys

For columns that should be unique by business logic (email, phone, username, tax_id, slug, external_id), check duplicates:

```sql
SELECT {col}, COUNT(*) AS cnt
FROM {table}
WHERE {col} IS NOT NULL
GROUP BY {col}
HAVING COUNT(*) > 1
ORDER BY cnt DESC;
```

Heuristic for picking candidate columns: column name contains `email`, `phone`, `ssn`, `tax`, `slug`, `external_id`, `username`, or column has distinct_count close to row_count in step 2.

Report: top 10 duplicated values. **WARNING** severity.

---

### Step 6 — Dates out of range

For each date/timestamp column, check:

**Future dates** (CRITICAL if created_at/updated_at/birth_date; WARNING for due_date/expires_at which can legitimately be future):

```sql
-- PG / MySQL
SELECT COUNT(*) AS future_dates FROM {table} WHERE {col} > NOW();

-- Oracle
SELECT COUNT(*) AS future_dates FROM {table} WHERE {col} > SYSDATE;
```

**Ancient / epoch-zero dates** (likely garbage):

```sql
-- PG / MySQL
SELECT COUNT(*) FROM {table}
WHERE {col} < DATE '2000-01-01' OR {col} = DATE '1970-01-01';

-- Oracle
SELECT COUNT(*) FROM {table}
WHERE {col} < DATE '2000-01-01' OR {col} = DATE '1970-01-01';
```

**Impossible ordering** (updated before created, end before start):

```sql
SELECT COUNT(*) FROM {table} WHERE updated_at < created_at;
SELECT COUNT(*) FROM {table} WHERE end_date < start_date;
```

Report counts + 5 example rows for each issue found.

---

### Step 7 — Referential integrity (orphans)

For each FK discovered in step 1, check for orphan rows (FK value that doesn't exist in target table):

```sql
SELECT COUNT(*) AS orphans
FROM {table} t
WHERE t.{fk_col} IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM {target_table} p
    WHERE p.{target_pk} = t.{fk_col}
  );
```

Any orphan count > 0 is **CRITICAL** — referential integrity is broken. Show 5 example orphan rows:

```sql
-- PG / MySQL
SELECT t.* FROM {table} t
WHERE t.{fk_col} IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM {target_table} p WHERE p.{target_pk} = t.{fk_col})
LIMIT 5;

-- Oracle
SELECT t.* FROM {table} t
WHERE t.{fk_col} IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM {target_table} p WHERE p.{target_pk} = t.{fk_col})
FETCH FIRST 5 ROWS ONLY;
```

Optionally use `check_cascade` to understand the broader FK graph.

---

### Step 8 — Numeric outliers

For numeric columns, flag values more than 3 standard deviations from mean:

```sql
-- PG / MySQL / Oracle all support STDDEV + AVG
WITH stats AS (
  SELECT AVG({col}) AS mu, STDDEV({col}) AS sigma
  FROM {table}
  WHERE {col} IS NOT NULL
)
SELECT COUNT(*) AS outlier_count
FROM {table}, stats
WHERE {col} IS NOT NULL
  AND ABS({col} - stats.mu) > 3 * stats.sigma;
```

Also flag:
- Negative values in columns where negatives make no sense (price, quantity, age, count)
- Zero values where zero is suspicious (price = 0, qty = 0)

```sql
SELECT COUNT(*) FROM {table} WHERE {col} < 0;
SELECT COUNT(*) FROM {table} WHERE {col} = 0;
```

Report: outlier count, min/max outlier values, 5 example rows. **INFO** by default, **WARNING** if >1% of rows are outliers.

---

### Step 9 — String anomalies

For varchar/text columns:

**Leading/trailing whitespace**:

```sql
-- PG / MySQL / Oracle
SELECT COUNT(*) FROM {table}
WHERE {col} IS NOT NULL AND {col} <> TRIM({col});
```

**Mixed-case inconsistencies** (same value with different casing — usually a bug in ingestion):

```sql
SELECT LOWER({col}) AS normalized, COUNT(DISTINCT {col}) AS variant_count, COUNT(*) AS total
FROM {table}
WHERE {col} IS NOT NULL
GROUP BY LOWER({col})
HAVING COUNT(DISTINCT {col}) > 1
ORDER BY variant_count DESC;
```

**Non-ASCII in supposedly-ASCII columns** (email, slug, identifier):

```sql
-- PostgreSQL
SELECT COUNT(*) FROM {table} WHERE {col} ~ '[^[:ascii:]]';

-- MySQL
SELECT COUNT(*) FROM {table} WHERE {col} <> CONVERT({col} USING ASCII);

-- Oracle
SELECT COUNT(*) FROM {table} WHERE ASCIISTR({col}) <> {col};
```

**Empty strings masquerading as values** (should probably be NULL):

```sql
SELECT COUNT(*) FROM {table} WHERE {col} = '';
```

Severity: **WARNING** for whitespace / mixed case; **INFO** for non-ASCII unless schema demands ASCII.

---

### Step 10 — Summary report

Present findings to the user as a **prioritized report**:

```
DATA QUALITY REPORT — {table} ({row_count} rows)
================================================

CRITICAL (fix immediately)
  - NOT NULL violation: column `email` has 3 nulls
  - Duplicate PK: id=42 appears 2 times
  - Orphan FK: customer_id → customers has 128 orphans

WARNING (investigate)
  - High nulls: `phone` is 47% null
  - Duplicate emails: 12 addresses appear 2+ times
  - Future timestamps: `created_at` has 5 rows > NOW()
  - Whitespace: `name` has 23 rows with leading/trailing spaces
  - Mixed case: `country` has "USA" and "usa" and "Usa"

INFO (review when convenient)
  - Outliers: `amount` has 8 values >3σ from mean (max: 1,250,000)
  - Non-ASCII: `slug` has 2 rows with non-ASCII chars

OK
  - No duplicate PKs on single-col keys
  - All other FKs resolved correctly
  - No impossible date orderings
================================================
```

Keep it scannable. Don't dump raw query output — summarize counts and show 3-5 examples max per issue.

---

### Safety — DO NOT modify data

This skill is **read-only**. It audits and reports. It MUST NOT:

- Run `UPDATE`, `DELETE`, `INSERT`, `MERGE`, `TRUNCATE`
- Run `ALTER TABLE`, `DROP`, `CREATE`
- Suggest auto-fixes without user confirmation

If the user asks "fix it", stop and tell them: "I found these issues. Fixing them requires UPDATE/DELETE statements — please confirm which ones you want me to write, and I'll draft them for your review before running."
