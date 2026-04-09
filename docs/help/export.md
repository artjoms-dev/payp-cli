# Exporting Data

payp can export any query result to CSV or JSON.

## How to export
Just ask the assistant:
- "export all customers to csv"
- "save top 100 orders as json"
- "export this query to ./data/report.csv"

## Default location
`./exports/` in your current directory. payp creates it automatically.

## Custom paths
Specify any path: "save to /tmp/backup.csv" or "export to my desktop".

## Formats
- **CSV** — standard, for Excel/Google Sheets
- **JSON** — for APIs, nested data

## Large exports
payp streams from DB cursor to file — no memory issues for millions of rows.
