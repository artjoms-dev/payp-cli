---
name: python-analytics
description: Run Python code for charts, advanced analytics, and custom exports (matplotlib, plotly, pandas)
when_to_use: User asks to create a chart image (PNG/SVG/HTML), export data to an unusual format, run statistical analysis, or do any computation SQL cannot do easily
allowed_tools: [execute_sql, export_query, execute_python]
db_types: [postgresql, mysql, oracle]
author: payp-team
version: 1.0
---

## Python Analytics Workflow

Use this skill when the user wants a **chart image file**, **custom analytics**, or **advanced data transformation** that SQL cannot do.

### Step 1 — Get the data

Query the database first via `execute_sql` OR export to a file:

- For small data (<1000 rows): use `execute_sql` and pass the result rows as a Python literal.
- For large data: `export_query` to CSV/Parquet, then Python reads the file.

### Step 2 — Write and execute Python

Call `execute_python` with complete, runnable code. Key rules:

- **Always `print()` a summary** at the end (what was created, row counts, insights).
- **Always save output files to `./exports/`** so users find them.
- **Use non-interactive backends** for matplotlib: `matplotlib.use('Agg')` at the top.
- **Import only what you need**: matplotlib, pandas, plotly, seaborn, numpy.
- If a package is missing, tell the user to run `pip install <package>`.

### Example: chart to PNG

```python
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

labels = ['NA', 'EU', 'APAC']
values = [45000, 38000, 12000]

fig, ax = plt.subplots(figsize=(8, 5))
ax.bar(labels, values, color='steelblue')
ax.set_title('Revenue by Region')
ax.set_ylabel('Revenue ($)')
ax.grid(axis='y', alpha=0.3)

Path('./exports').mkdir(exist_ok=True)
out = './exports/revenue_by_region.png'
plt.savefig(out, dpi=120, bbox_inches='tight')
plt.close()
print(f'Chart saved: {out}')
```

### Example: interactive HTML chart with plotly

```python
import plotly.graph_objects as go
from pathlib import Path

fig = go.Figure(data=[go.Bar(x=['NA','EU','APAC'], y=[45000,38000,12000])])
fig.update_layout(title='Revenue by Region', yaxis_title='Revenue ($)')

Path('./exports').mkdir(exist_ok=True)
out = './exports/revenue_by_region.html'
fig.write_html(out)
print(f'Interactive chart: {out}')
```

### Example: pandas analysis

```python
import pandas as pd
df = pd.read_csv('./exports/orders_export.csv')
summary = df.groupby('status').agg(
    order_count=('id', 'count'),
    total_revenue=('total_amount', 'sum'),
    avg_amount=('total_amount', 'mean'),
)
print(summary.to_string())
summary.to_csv('./exports/orders_by_status.csv')
print(f'\nSaved to ./exports/orders_by_status.csv')
```

### Step 3 — Report to user

After Python runs, tell the user:
- What file was created and where
- Key insights found in the data
- Any errors/warnings from stderr

### Safety

Python execution is a powerful tool. Use it ONLY when:
- SQL cannot produce the output (e.g., PNG/HTML image files)
- Pandas/numpy math is needed
- The user explicitly asks for a chart image or custom analysis

Do NOT use Python for simple queries — always prefer `execute_sql` for data retrieval.
