# Security Modes

Four modes for different trust levels. Switch with `/mode <name>`.

## manual (default)
Every DDL/DML statement is shown to you first. Press:
- `Enter` to execute
- `e` to edit the SQL
- `c` / `Esc` to cancel

SELECTs execute immediately.

## yolo
All operations execute automatically. No approvals. Use for local dev sandboxes.

## secure
Like manual, but the reviewer model (Model B) checks the SQL first:
- **APPROVE** — reviewer OK, you still approve
- **SAFER** — reviewer suggests an improved version, shown alongside
- **HARD_BLOCK** — DROP DATABASE / DROP SCHEMA / TRUNCATE → type OVERRIDE to proceed

## secure-auto
Reviewer decides automatically:
- **APPROVE** → execute
- **SAFER** → use the safer version automatically
- **HARD_BLOCK** → still requires typing OVERRIDE

## Which to use
- Learning/exploring: `manual`
- Local dev, trusted environment: `yolo`
- Production, shared DB: `secure` or `secure-auto`
