---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "964a9ba25b42d558a4b57109df7f7b108f19a8b1130a2a3296e976f11cd76fbd"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `964a9ba25b42d558a4b57109df7f7b108f19a8b1130a2a3296e976f11cd76fbd`

## Current top-level state

- Production logical lines: `270287`
- Engine logical lines: `6779`
- Direct GameState-write heuristic: `74`
- Registered typed semantic handlers: `133`
- Registered runtime components: `197`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
