---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "2c9a4d6f4be3992473ef1eaee62c581425685e179a904b03dff747373058b35c"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `2c9a4d6f4be3992473ef1eaee62c581425685e179a904b03dff747373058b35c`

## Current top-level state

- Production logical lines: `246401`
- Engine logical lines: `6916`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `129`
- Registered runtime components: `130`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
