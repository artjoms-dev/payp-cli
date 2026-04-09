---
name: web-scraper
description: Fetch data from URLs and REST APIs for import into the database
when_to_use: User asks to pull data from a URL, API endpoint, public dataset, or JSON feed — then insert/transform it into the database
allowed_tools: [fetch_url, execute_sql, bulk_insert, execute_python]
db_types: [postgresql, mysql, oracle]
author: payp-team
version: 1.0
---

## Web Scraper Workflow

Use this skill when the user wants to pull data **from an external URL or REST API** and land it in the database.

### Step 1 — Fetch the data

Call `fetch_url` with the target URL. For JSON APIs, the response is auto-parsed into `parsed_json`.

```
fetch_url(url="https://api.example.com/v1/users")
```

Returns: `status_code`, `headers`, `content_type`, `body` (first 50KB), `parsed_json` (if JSON), `total_bytes`, `truncated`.

### Step 2 — Inspect & plan the schema

- For JSON: look at `parsed_json`. Identify the array of records and their field names/types.
- For CSV: peek at `body` — first few lines reveal columns.
- Propose a target table (DDL) or confirm the existing one via `lookup_schema`.

### Step 3 — Create the target table (if needed)

```sql
CREATE TABLE IF NOT EXISTS api_users (
  id BIGINT PRIMARY KEY,
  email TEXT,
  name TEXT,
  created_at TIMESTAMPTZ,
  raw JSONB
);
```

### Step 4 — Insert the rows

Use `bulk_insert` for sets of records parsed from JSON/CSV. For transformations (flattening nested objects, parsing timestamps, joining multiple pages), hand the `parsed_json` to `execute_python`.

---

## Common Patterns

### A. Public JSON API → table

```
1. fetch_url(url="https://jsonplaceholder.typicode.com/posts")
2. parsed_json is a list of {userId, id, title, body}
3. bulk_insert(table="posts", rows=parsed_json)
```

### B. Authenticated API with Bearer token

```
fetch_url(
  url="https://api.service.com/v2/orders",
  method="GET",
  headers={"Authorization": "Bearer <token>", "Accept": "application/json"}
)
```

Ask the user for the token — NEVER hardcode secrets. Prefer environment variables via `execute_python` if the user wants to read `os.environ`.

### C. POST to an API

```
fetch_url(
  url="https://api.service.com/graphql",
  method="POST",
  headers={"Content-Type": "application/json"},
  body={"query": "{ users { id name } }"}
)
```

### D. Pagination

Many APIs paginate. Loop:

```
page = 1
all_rows = []
while True:
  r = fetch_url(url=f"https://api.example.com/items?page={page}&per_page=100")
  items = r.parsed_json["items"]
  if not items: break
  all_rows.extend(items)
  page += 1
bulk_insert(table="items", rows=all_rows)
```

Always cap the loop (e.g., max 50 pages) to avoid runaway fetches.

### E. CSV from URL

```
r = fetch_url(url="https://data.gov/dataset.csv")
# r.body is the CSV text (truncated at 50KB — use execute_python for larger)
```

For large CSVs, have `execute_python` stream the URL with `pd.read_csv(url)` directly and then call `bulk_insert`.

---

## Safety Rules

- **Localhost / private IPs are blocked** by default. To scrape a local dev API, pass `allow_internal=true` after user confirmation.
- **No secrets in code.** If an API key is needed, ask the user and pass it via the `headers` parameter for that call only.
- **Respect rate limits.** If the API returns 429, back off and stop — do NOT retry in a tight loop.
- **Robots / ToS.** For HTML scraping of arbitrary sites, remind the user to check the site's ToS and `robots.txt`.
- **Body size cap.** Text bodies are truncated at 50KB. For larger payloads, use `execute_python` with httpx/requests and stream to a file in `./exports/`, then import.

---

## Reporting to user

After fetching, summarize:
- HTTP status + content type
- How many records/rows were parsed
- Sample of the first record (field names)
- What table you'll insert into (or propose creating)
- Then confirm before running bulk_insert
