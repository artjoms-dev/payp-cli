# payp Built-in Skills

This directory ships with payp and contains the default skill library.

## What is a skill?

A skill is a markdown file with YAML frontmatter that defines a reusable
workflow. The LLM loads a skill via the `invoke_skill` tool and follows its
instructions step-by-step, using normal payp tools (query, schema_lookup, etc.)
to execute the plan.

Skills do **not** bypass security modes — every destructive SQL still goes
through the active approval flow.

## Skill file format

```markdown
---
name: my-skill
description: One-line summary
when_to_use: When the LLM should invoke this
allowed_tools: [query, schema_lookup]
db_types: [postgresql, mysql, oracle]
author: you
version: 1.0
---

## My Skill Workflow

1. First do X
2. Then do Y
3. Finally summarize Z
```

**Required fields**: `name`, `description`, `when_to_use`.
**Optional fields**: `allowed_tools`, `db_types`, `author`, `version`.

## Skill discovery locations

Payp loads skills from three locations (later wins on name conflict):

1. `builtin_skills/` — shipped with payp (this directory)
2. `~/.payp/skills/` — user-level, personal skills
3. `./payp/skills/` — project-level, team-shared skills (in CWD)

## Browsing skills

Run `/skills` in the interactive CLI to browse available skills, filtered by
the current connection's dialect.

See `docs/skills-architecture.md` for the full design.
