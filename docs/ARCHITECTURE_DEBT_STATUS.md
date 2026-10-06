---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "4f52fabb532ed2e5a131ef837cb46301d61ae3bf05259c8b645f1d2ca0e02722"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `4f52fabb532ed2e5a131ef837cb46301d61ae3bf05259c8b645f1d2ca0e02722`

## Current top-level state

- Production logical lines: `239572`
- Engine logical lines: `6938`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `126`
- Registered runtime components: `118`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
