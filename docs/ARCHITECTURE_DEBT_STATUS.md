---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "7291d851b745dad411d8813d66dc374ccb576a4f3eacd6588c95650a0ad7d86a"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `7291d851b745dad411d8813d66dc374ccb576a4f3eacd6588c95650a0ad7d86a`

## Current top-level state

- Production logical lines: `366808`
- Engine logical lines: `6461`
- Direct GameState-write heuristic: `71`
- Registered typed semantic handlers: `175`
- Registered runtime components: `291`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
