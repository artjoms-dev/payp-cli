---
name: anonymize-data
description: Replace PII in a table (emails, names, phones) with realistic fake values — creates copy, view, or export
when_to_use: User asks to anonymize, mask, scrub, or de-identify data in a table for GDPR/testing/sharing
allowed_tools: [schema_lookup, execute_sql, bulk_insert, snapshot_before_delete, export_query]
db_types: [postgresql, mysql, oracle]
author: payp-team
version: 1.0
---

## Anonymize Data Workflow

Anonymize PII columns in a table. The user chooses the output strategy upfront — do NOT assume one.

### Step 0: ALWAYS ASK THE USER FIRST

Before inspecting schema or doing any work, ask:

> "How do you want to anonymize this data?
>   1. **IN-PLACE** — update the original table (destructive, backup first)
>   2. **NEW TABLE** — create `{table}_anon` with anonymized data (safe, uses storage)
>   3. **VIEW** — create `{table}_anon` view that's always current (safe, zero storage)
>   4. **EXPORT ONLY** — write to CSV/Parquet, don't touch DB (safest, portable)
> Which option?"

Wait for the answer. Do not proceed without it.

### Step 1: Detect PII columns

Use `schema_lookup` on the target table. Flag columns whose names match these patterns
(case-insensitive, substring match):

| Category | Patterns |
|----------|----------|
| Names    | `name`, `first_name`, `last_name`, `full_name`, `customer_name`, `contact_name` |
| Emails   | `email`, `email_address`, `e_mail` |
| Phones   | `phone`, `phone_number`, `mobile`, `tel`, `telephone` |
| Addresses| `address`, `street`, `city`, `postal_code`, `zip`, `zipcode` |
| Gov IDs  | `ssn`, `tax_id`, `passport`, `license`, `national_id` |
| Payment  | `card_number`, `credit_card`, `cvv`, `bank_account`, `iban` |
| Network  | `ip_address`, `ip`, `user_agent` |

Do NOT flag primary keys or foreign keys — they must stay intact for referential integrity.

### Step 2: Confirm PII columns with user

Show detected columns and ask:

> "Detected PII columns in `{table}`: **email, phone, name**. Add or remove any?"

Let user override. Columns they remove stay un-anonymized. Columns they add get anonymized
with a generic `'REDACTED_' || id` pattern unless user specifies otherwise.

### Step 3: Build the anonymized SELECT expression

Use **deterministic fake data** keyed off the row's primary key (usually `id`) so that
referential integrity is preserved across related tables.

| PII type | Anonymized expression (PG) |
|----------|----------------------------|
| name / first_name / last_name | `'User ' \|\| CAST(id AS TEXT)` |
| email    | `'user' \|\| CAST(id AS TEXT) \|\| '@example.com'` |
| phone    | `'555-' \|\| LPAD(CAST(id AS TEXT), 4, '0')` |
| address  | `'REDACTED_ADDR_' \|\| CAST(id AS TEXT)` |
| city     | `'City_' \|\| CAST((id % 100) AS TEXT)` |
| postal / zip | `LPAD(CAST((id % 99999) AS TEXT), 5, '0')` |
| ssn / tax_id | `'XXX-XX-' \|\| LPAD(CAST((id % 9999) AS TEXT), 4, '0')` |
| card_number | `'4111-1111-1111-' \|\| LPAD(CAST((id % 9999) AS TEXT), 4, '0')` |
| ip_address | `'10.0.' \|\| CAST((id / 256 % 256) AS TEXT) \|\| '.' \|\| CAST((id % 256) AS TEXT)` |

### Step 4: Execute chosen strategy

#### Option 1 — IN-PLACE UPDATE (destructive)

ALWAYS call `snapshot_before_delete` first to back up the table. Then:

```sql
-- PostgreSQL
UPDATE customers SET
  email = 'user' || CAST(id AS TEXT) || '@example.com',
  name  = 'User ' || CAST(id AS TEXT),
  phone = '555-' || LPAD(CAST(id AS TEXT), 4, '0');
```

Warn loudly: "This modifies original data. Snapshot saved at `{snapshot_path}`."

#### Option 2 — NEW TABLE COPY

```sql
-- PostgreSQL
CREATE TABLE customers_anon AS
SELECT
  id,
  'User ' || CAST(id AS TEXT) AS name,
  'user' || CAST(id AS TEXT) || '@example.com' AS email,
  '555-' || LPAD(CAST(id AS TEXT), 4, '0') AS phone,
  region, segment, created_at  -- non-PII columns kept as-is
FROM customers;
```

#### Option 3 — VIEW (always current, zero storage)

```sql
CREATE OR REPLACE VIEW customers_anon AS
SELECT
  id,
  'User ' || CAST(id AS TEXT) AS name,
  'user' || CAST(id AS TEXT) || '@example.com' AS email,
  '555-' || LPAD(CAST(id AS TEXT), 4, '0') AS phone,
  region, segment, created_at
FROM customers;
```

Oracle note: use `CREATE OR REPLACE VIEW` — same syntax works.

#### Option 4 — EXPORT ONLY

Build the anonymized SELECT (same expression as Option 2, but without CREATE TABLE),
then call `export_query` to write to CSV or Parquet. Do not touch the database at all.

```sql
SELECT
  id,
  'User ' || CAST(id AS TEXT) AS name,
  'user' || CAST(id AS TEXT) || '@example.com' AS email,
  ...
FROM customers;
```

Then: `export_query(sql=..., path='customers_anon.csv', format='csv')`

### Step 5: Verify results

Sample 5 rows from the output (query the new table/view, or re-read first 5 lines
of the export file). Show to the user for confirmation.

### Step 6: Report

> "Anonymized **{N}** rows from `{table}`.
> Strategy: **{IN-PLACE | NEW TABLE | VIEW | EXPORT}**.
> Output: `{table_name | view_name | file_path}`.
> Columns anonymized: {list}.
> {If IN-PLACE: Snapshot at {path} — restore with `payp restore {snapshot_id}`.}"

---

## Dialect Notes

| Operation | PostgreSQL | MySQL | Oracle |
|-----------|-----------|-------|--------|
| int→string cast | `CAST(id AS TEXT)` or `id::TEXT` | `CAST(id AS CHAR)` or `CONCAT(id)` | `TO_CHAR(id)` |
| concat | `\|\|` or `CONCAT()` | `CONCAT()` (not `\|\|` by default) | `\|\|` |
| left-pad | `LPAD(str, n, '0')` | `LPAD(str, n, '0')` | `LPAD(str, n, '0')` |
| modulo | `%` | `%` or `MOD()` | `MOD(a, b)` |
| create view | `CREATE OR REPLACE VIEW` | `CREATE OR REPLACE VIEW` | `CREATE OR REPLACE VIEW` |

For MySQL, prefer `CONCAT(...)` over `||` since `||` means OR unless `PIPES_AS_CONCAT`
sql_mode is set. Example MySQL expression:

```sql
CONCAT('user', CAST(id AS CHAR), '@example.com') AS email
```

For Oracle, `MOD(id, 100)` instead of `id % 100`.

---

## Safety Rules

1. **IN-PLACE mode requires snapshot first.** Never skip `snapshot_before_delete`.
2. **Never DROP the original table.** Only CREATE new objects or UPDATE in place.
3. **Preserve primary keys and foreign keys.** Only anonymize non-key PII columns.
4. **Use deterministic fake data keyed on `id`** so joins to related tables still work
   (e.g. `orders.customer_id` still points to valid `customers.id`).
5. **Warn about FK fanout.** If other tables have FK columns referencing this table's
   PII (rare but possible — e.g. `user_email` stored redundantly in `audit_log`),
   flag it: "Table `audit_log.user_email` may duplicate this PII — anonymize separately."
6. **Refuse to anonymize password hashes.** They're already hashed; re-masking is pointless
   and risks breaking auth. Flag and skip.
7. **Confirm row count before IN-PLACE on large tables** (> 1M rows). Anonymizing
   10M rows in-place can lock the table for minutes.
