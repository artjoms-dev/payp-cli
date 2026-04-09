---
name: r-analytics
description: Run R code for statistical analysis, ggplot2 charts, and custom exports
when_to_use: User asks for statistical modeling (regression, ANOVA), ggplot2 charts, time series analysis, or advanced statistical visualizations
allowed_tools: [execute_sql, export_query, execute_r]
db_types: [postgresql, mysql, oracle]
author: payp-team
version: 1.0
---

## R Analytics Workflow

Use this skill when the user wants **statistical modeling**, **ggplot2 visualizations**, or analysis that R does better than Python/SQL.

### Step 1 — Get the data

Query the database first OR export to a file:
- Small data: use `execute_sql` and paste rows into R as a data frame literal
- Large data: `export_query` to CSV, then R reads with `read.csv()` or `readr::read_csv()`

### Step 2 — Write and execute R code

Call `execute_r` with complete, runnable code:
- Always `cat()` or `print()` a summary at the end
- Save output files to `./exports/`
- Use `ggsave()` for ggplot charts — saves as PNG/PDF
- If a package is missing, tell the user to run `install.packages("pkgname")` in R

### Example: ggplot2 bar chart

```r
library(ggplot2)

df <- data.frame(
  region = c("NA", "EU", "APAC"),
  revenue = c(45000, 38000, 12000)
)

p <- ggplot(df, aes(x = region, y = revenue, fill = region)) +
  geom_bar(stat = "identity") +
  labs(title = "Revenue by Region", y = "Revenue ($)") +
  theme_minimal()

dir.create("./exports", showWarnings = FALSE)
ggsave("./exports/revenue_by_region_r.png", plot = p, width = 8, height = 5, dpi = 120)
cat("Chart saved: ./exports/revenue_by_region_r.png\n")
```

### Example: linear regression

```r
df <- read.csv("./exports/orders_export.csv")

model <- lm(total_amount ~ customer_id + status, data = df)
cat("=== Model Summary ===\n")
print(summary(model))

# Save coefficients
coef_df <- data.frame(
  term = names(coef(model)),
  estimate = coef(model)
)
write.csv(coef_df, "./exports/regression_coefficients.csv", row.names = FALSE)
cat("\nCoefficients saved to ./exports/regression_coefficients.csv\n")
```

### Example: time series with dplyr + ggplot

```r
library(dplyr)
library(ggplot2)
library(lubridate)

df <- read.csv("./exports/orders_daily.csv")
df$date <- as.Date(df$date)

monthly <- df %>%
  mutate(month = floor_date(date, "month")) %>%
  group_by(month) %>%
  summarise(revenue = sum(total_amount), orders = n())

p <- ggplot(monthly, aes(x = month, y = revenue)) +
  geom_line(color = "steelblue", size = 1) +
  geom_point(size = 3) +
  labs(title = "Monthly Revenue Trend", x = "Month", y = "Revenue ($)") +
  theme_minimal()

ggsave("./exports/monthly_trend.png", p, width = 10, height = 6, dpi = 120)
cat(sprintf("Monthly trend chart saved (%d months analyzed)\n", nrow(monthly)))
```

### Step 3 — Report to user

After R runs, tell the user:
- What file was created
- Key statistical findings (coefficients, p-values, R², trends)
- Any warnings from stderr

### Safety

R execution is powerful — use it ONLY when:
- Statistical modeling is needed (lm, glm, ANOVA, time series)
- ggplot2 gives a significantly better chart than matplotlib
- User explicitly asks for R

Prefer `execute_sql` for simple data retrieval, `execute_python` for matplotlib/pandas work.
