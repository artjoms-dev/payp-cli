# payp — Query Library

## Overview

Saved queries live in `./payp/queries/` as plain `.sql` files with comment headers for metadata.

## File Format

```sql
-- payp:tags: finance, monthly, revenue, table:invoices, table:payments
-- payp:desc: Monthly revenue breakdown by payment method for finance team reporting

SELECT 
    DATE_TRUNC('month', p.payment_date) AS month,
    p.payment_method,
    COUNT(*) AS transaction_count,
    SUM(p.amount) AS total_revenue
FROM payments p
    JOIN invoices i ON i.id = p.invoice_id
WHERE p.status = 'completed'
GROUP BY 1, 2
ORDER BY 1 DESC, 4 DESC;
```

### Header Rules
- Lines starting with `-- payp:tags:` → comma-separated tags for search
- Lines starting with `-- payp:desc:` → human-readable description
- Both are optional but recommended
- Everything after the header comments is the SQL body
- Tags can reference tables (`table:payments`), years, teams, categories

## Directory Structure

```
./payp/queries/
├── monthly-revenue.sql
├── daily-active-users.sql
├── slow-queries-report.sql
├── customer-churn-analysis.sql
└── data-quality-check.sql
```

## Usage

### Saving a Query
```
payp> that query was useful, save it as "monthly-revenue"

  ✓ Saved to ./payp/queries/monthly-revenue.sql
  Add tags? (comma-separated, or Enter to skip)
  > finance, monthly, revenue, table:invoices, table:payments
  ✓ Tags added.
```

### Finding Saved Queries
```
payp> /queries
  Saved queries:
    1. monthly-revenue — Monthly revenue breakdown by payment method
       tags: finance, monthly, revenue, table:invoices, table:payments
    2. daily-active-users — DAU count with 7-day rolling average
       tags: analytics, users, daily
    3. slow-queries-report — Queries running longer than 5s
       tags: dba, performance, monitoring
    ...

payp> /queries finance
  Filtered by "finance":
    1. monthly-revenue — Monthly revenue breakdown by payment method
```

### Running a Saved Query
```
payp> run the monthly-revenue query

LLM reads ./payp/queries/monthly-revenue.sql
LLM executes the SQL
Results displayed normally
```

### LLM Can Reference Saved Queries
The LLM knows about saved queries through the knowledge base. When a user asks something that matches a saved query's tags or description, the LLM can suggest:

```
payp> I need the revenue numbers

  I found a saved query "monthly-revenue" that matches.
  Want me to run it, or do you need something different?
```

## Tag Conventions

| Tag Pattern | Meaning | Example |
|-------------|---------|---------|
| `table:xxx` | References this table | `table:payments` |
| `schema:xxx` | References this schema | `schema:reporting` |
| team name | Which team uses this | `finance`, `analytics` |
| frequency | How often it's run | `daily`, `weekly`, `monthly` |
| category | Type of query | `monitoring`, `reporting`, `etl` |
| year | Time relevance | `2025`, `q1` |
