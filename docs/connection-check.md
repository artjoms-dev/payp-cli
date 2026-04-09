# payp — Connection Check & Auto-Discovery

## On First Connect

When user connects to a DB for the first time, payp runs a quick metadata script to build initial knowledge.

### Flow
```
$ /db
> Select: + New connection
> Type: PostgreSQL
> Host: prod-db.company.com
> ...
> ✓ Connected to prod-analytics (PostgreSQL 16.2)
> 
> Running initial discovery...
>   ✓ 5 schemas found
>   ✓ 247 tables, 31 views
>   ✓ Server uptime: 45 days
>   ✓ Database size: 12.4 GB
>   ✓ Schema cache saved
> 
> Ready! Type naturally to start.
```

### What the Discovery Script Collects

**Basic only. Fast. No deep introspection.**

| Info | PostgreSQL | MySQL | Oracle |
|------|-----------|-------|--------|
| DB version | `SELECT version()` | `SELECT version()` | `SELECT * FROM v$version` |
| Schema list + table counts | `information_schema.tables` GROUP BY schema | `information_schema.tables` GROUP BY schema | `all_tables` GROUP BY owner |
| Total tables & views | COUNT from information_schema | COUNT from information_schema | COUNT from all_tables + all_views |
| Database size | `pg_database_size()` | `information_schema.tables` SUM data_length | `dba_segments` SUM bytes |
| Server uptime | `pg_postmaster_start_time()` | `SHOW STATUS LIKE 'Uptime'` | `v$instance.startup_time` |
| Current user & role | `current_user`, `current_setting('is_superuser')` | `CURRENT_USER()`, grants | `USER`, `session_roles` |

### What It Does NOT Collect
- No column-level introspection (that's T2, loaded on demand)
- No index details
- No row counts per table (expensive on large DBs)
- No query plans or performance data
- No data sampling

## On Reconnect (returning connection)

Quick check only — compare table count with cached value.

```
$ /db prod-analytics
> ✓ Connected to prod-analytics (PostgreSQL 16.2)
> ✓ Schema cache up to date (247 tables)
```

Or if changed:
```
> ✓ Connected to prod-analytics (PostgreSQL 16.2)
> ⚠ Schema changed: 247 → 249 tables (2 new). Run /schema --refresh for details.
```

### Reconnect Check Query
```sql
-- PostgreSQL
SELECT schemaname, COUNT(*) 
FROM pg_tables 
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
GROUP BY schemaname;
```

Compare with cached T0 index. If counts differ → inform user.

## Cache Storage

Discovery results saved to cache:
```
~/.payp/cache/
├── prod-analytics_t0.json          # Schema index (names + counts)
├── prod-analytics_t1.json          # Full table name catalog
├── prod-analytics_meta.json        # DB version, size, user, last check timestamp
```

Cache is used for:
- T0/T1 schema context in LLM system prompt
- Reconnect freshness check
- Schema search (`/schema` command)
