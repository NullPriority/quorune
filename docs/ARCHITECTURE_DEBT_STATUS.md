---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "df9a4c352545a27cc07e7078caeee48fedbc51ddb1659302030e1cc769ca3aa0"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `df9a4c352545a27cc07e7078caeee48fedbc51ddb1659302030e1cc769ca3aa0`

## Current top-level state

- Production logical lines: `249124`
- Engine logical lines: `6874`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `130`
- Registered runtime components: `139`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
