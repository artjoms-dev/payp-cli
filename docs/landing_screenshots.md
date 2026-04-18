# Landing-page screenshots — demo database recipe

Self-contained, seeded, gorgeous. Every command below is designed to
make a single payp response look stunning on a screenshot.

## 1. Boot the demo db

```bash
# one-time
docker compose up -d postgres-demo

# verify
docker compose logs postgres-demo | tail -20
psql "postgres://payp:payp_dev@localhost:5433/aurora" -c "\dt"
```

Connection string:
```
postgres://payp:payp_dev@localhost:5433/aurora
```

Reset (wipe + reseed) any time:
```bash
docker compose down postgres-demo && docker volume rm payp_cli_demodata
docker compose up -d postgres-demo
```

## 2. Shot list — one query per hero feature

Register the connection inside payp first:
```
/connect aurora postgres://payp:payp_dev@localhost:5433/aurora
/use aurora
```

### 🎨 Line chart — MRR hockey stick
```
plot daily MRR for the last 365 days as a line chart
```
Hits `revenue_daily` — a beautifully noisy hockey-stick curve.

### 📊 Bar chart — MRR by region
```
show me MRR by region as a bar chart
```
Uses the `v_mrr_by_region` view — 4 clean bars.

### 🥧 Pie / stacked bar — plan mix
```
what's our plan distribution by MRR?
```
`v_plan_mix` — free / starter / growth / enterprise.

### 🔥 Heatmap — product usage hour × weekday
```
plot events by hour of day and day of week as a heatmap
```
`events` has a strong 9–18 weekday bias → obvious hot zone.

### 📦 Boxplot — MRR distribution by plan
```
show MRR distribution per plan as a boxplot
```

### 🏆 Top customers table
```
who are our top 10 customers by MRR?
```
`v_top_customers` — renders as a gorgeous Rich table.

### 💰 Overdue invoices
```
list every overdue invoice with the customer name and amount
```

### 🔎 Schema exploration
```
/schema                      # tree of all tables
/describe organizations      # columns + comments
```

### 🔐 Security check (the money shot)
```
run a security audit on this database
```
The demo schema is booby-trapped with:
- `users.password_hash` — legacy SHA-1, no salt
- `api_keys.key_plaintext` — raw secrets in the clear
- `events.org_id` — no FK (broken referential integrity)
- `events.occurred_at` — hot column with no index
- `users.email` — PII stored unencrypted

Every single one will get flagged → a long, beautiful, color-coded report.

### 🧬 Migration suggestion
```
suggest a migration to partition the events table by month
```
or
```
generate a migration to rehash legacy SHA-1 passwords with argon2id
```

### 🧠 Memory creation
```
remember that our fiscal year starts April 1 and MRR is tracked in USD only
```
Then in a follow-up:
```
what's our Q1 FY26 MRR growth?
```
→ shows payp recalling the memory and applying it.

### 🤖 Multi-model review (executor + reviewer)
```
write a query that finds organizations likely to churn next month
and have it reviewed
```
Shows the reviewer pass with its diff / approval flow.

### 📤 Export
```
export the last 30 days of revenue_daily as csv
```

## 3. Schema map (for your reference)

```
organizations ──┬── users
                ├── subscriptions
                ├── invoices
                └── api_keys          (⚠ plaintext keys)

events          (⚠ no FK, no index)
revenue_daily   (pre-aggregated, 365 rows)
feature_flags

v_mrr_by_region      v_mrr_by_industry
v_plan_mix           v_top_customers
```

## 4. Tips for gorgeous screenshots

- Run `/clear` between shots — clean terminal, no residual chatter.
- Use a ~120 col × 32 row terminal so Rich tables wrap nicely.
- Dark theme. True-color terminal (iTerm2, Ghostty, WezTerm, Kitty).
- For charts: let payp finish streaming before capturing.
- Take the security-audit screenshot **last** — it's long and worth a
  whole landing section on its own.
