# Dialect Support & Quirks

payp supports PostgreSQL, MySQL, and Oracle through dialect-specific drivers
behind a uniform `ConnectionManager` interface. This doc captures the quirks
discovered while wiring them up.

## Drivers

| Dialect | Python package | Mode | Placeholder |
|---------|---------------|------|-------------|
| PostgreSQL | `psycopg[binary]>=3.1` | async | `%s` |
| MySQL | `aiomysql>=0.2.0` | async | `%s` |
| Oracle | `oracledb>=2.0` thin mode | async (`connect_async`) | `:1, :2, ...` |

The Oracle driver auto-translates `%s` placeholders in introspection queries
into Oracle-style `:1, :2, ...` binds, so dialect-dispatch code can keep
using a single placeholder style in strings where it helps.

## PostgreSQL

- Catalog: `information_schema` + `pg_*` extensions (pg_indexes, pg_stat_user_tables).
- System schemas excluded: `pg_catalog`, `information_schema`, `pg_toast`.
- `constraint_column_usage` is the idiomatic way to discover the FK target table.
- `BIGSERIAL` is faked from `BIGINT` + `nextval` default.

## MySQL

- Catalog: `information_schema` only.
- System schemas excluded: `information_schema`, `mysql`, `performance_schema`, `sys`.
- **FK quirk**: the referenced table lives in `key_column_usage.referenced_table_name`
  — NOT `table_name`. We filter FK rows with `referenced_table_name IS NOT NULL`
  instead of joining `table_constraints` (simpler and works).
- **Case-insensitive column keys**: aiomysql's DictCursor returns column names in the
  case the server emits them. Queries against `information_schema` normally return
  uppercase identifiers on some builds, so `execute_raw()` normalises all dict keys
  to lowercase for internal use. The user-facing `execute()` preserves case from
  `cursor.description`.
- `lower_case_table_names` server setting influences how tables are stored/matched
  on case-insensitive filesystems (macOS, Windows). Not currently normalised.
- `check_constraints` exists in MySQL 8.0.16+ but we skip it in T3 for now.

## Oracle

- Catalog: `all_tables`, `all_views`, `all_tab_columns`, `all_constraints`,
  `all_cons_columns`, `all_indexes`, `all_ind_columns`, `all_triggers`.
- System schemas excluded: large hard-coded list including
  `SYS`, `SYSTEM`, `OUTLN`, `XDB`, `CTXSYS`, `MDSYS`, `ORDSYS`, `ORDDATA`,
  `DBSNMP`, `APPQOSSYS`, `AUDSYS`, `GSMADMIN_INTERNAL`, `LBACSYS`,
  `OJVMSYS`, `OLAPSYS`, `WMSYS`, `DVSYS`, `DVF`, `GGSYS`, `DIP`,
  `ANONYMOUS`, `XS$NULL`, `PUBLIC`, `PDBADMIN`, `MDDATA`, etc. See
  `ORACLE_SYSTEM_SCHEMAS` in `introspection.py` for the full list.
- **Identifiers are uppercase unless quoted.** Callers to `discover_t2`/`discover_t3`
  may pass lowercase names; the dispatcher uppercases them before probing
  `all_tables`. Returned column metadata is lowercased on the way out to match the
  other dialects.
- **Nullable is 'Y'/'N'** (not YES/NO). Normalised to YES/NO in `_t2_oracle`.
- `data_default` is returned as a LONG — oracledb exposes it as `str` in thin mode.
- **No trailing semicolons** on `cursor.execute()` calls. Stripped automatically.
- FK join pattern: constraints of type `R` — the referenced columns live in
  `all_cons_columns` under `r_owner` + `r_constraint_name`. We position-match
  local and remote columns.
- Check constraints have `generated='USER NAME'` for user-defined ones; NOT NULL
  is also stored as a CHECK constraint, which we filter out in T3.
- Version probe: `SELECT banner_full FROM v$version WHERE ROWNUM=1`.

### gvenzl/oracle-free container quirks

- Init scripts under `/container-entrypoint-initdb.d/` run as **SYS in the CDB
  root**, not in the app PDB. The `seed_oracle.sql` file therefore starts with
  `ALTER SESSION SET CONTAINER = PAYP_TEST;` and
  `ALTER SESSION SET CURRENT_SCHEMA = PAYP;` to land the tables in the right
  PDB/owner. Without this, tables end up owned by SYS in the CDB.
- The Oracle volume must be wiped (`docker volume rm payp_cli_oracledata`) when
  the seed changes — init runs only once per volume lifetime.
- Service/PDB name for the default app DB is taken from `ORACLE_DATABASE` env
  var (we use `payp_test`). The connection DSN uses this as the service name:
  `host:1521/payp_test`. Fallback service name in the image is `FREEPDB1`.
- Health check: `healthcheck.sh` baked into the image. First healthy may take
  40-60s on a cold start.

## Unified type formatting

`_format_column_type()` in `introspection.py` collapses dialect-specific types
to a common vocabulary so the T2 DDL looks roughly the same across databases:

| Source | Normalised |
|--------|-----------|
| `character varying`, `varchar`, `varchar2`, `nvarchar2` | `VARCHAR(n)` |
| `numeric`, `decimal`, `number` | `NUMERIC(p,s)` |
| `timestamp with time zone` | `TIMESTAMPTZ` |
| `datetime`, `timestamp without time zone`, `timestamp` | `TIMESTAMP` |
| `tinyint`, `smallint`, `mediumint`, `int`, `integer` | `INT`/`SMALLINT` |
| `clob`, `longtext`, `mediumtext`, `text` | `TEXT` |
| `bool`, `boolean` | `BOOLEAN` |

## Known non-quirks / follow-ups

- Oracle `NUMBER` without precision maps to `NUMERIC` — Oracle IDENTITY columns
  show up as plain `NUMERIC` in T2 output (no `BIGSERIAL`-equivalent flag yet).
- MySQL column default `'free'` comes back without surrounding quotes in
  information_schema — shown as `DEFAULT free` in T2. Fine for LLM context but
  could be wrapped in quotes for strict DDL output.
- Connection wizard defaults: PostgreSQL 5432, MySQL 3306, Oracle 1521. Oracle
  additionally requires a **service name** (commonly `FREEPDB1` or the value of
  `ORACLE_DATABASE`) which we reuse the `database` field for.
