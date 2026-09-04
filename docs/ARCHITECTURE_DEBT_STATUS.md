---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "7bcc7bdab8abbccdbcfd4e924c2b0c0edf452a7073812faa1358381d40401358"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `7bcc7bdab8abbccdbcfd4e924c2b0c0edf452a7073812faa1358381d40401358`

## Current top-level state

- Production logical lines: `201027`
- Engine logical lines: `7049`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `116`
- Registered runtime components: `98`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
