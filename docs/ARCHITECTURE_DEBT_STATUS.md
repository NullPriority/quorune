---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "2524b8812ea3480dc3ac65a66ab536fc169e8f430509f38be41314ea501d7927"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `2524b8812ea3480dc3ac65a66ab536fc169e8f430509f38be41314ea501d7927`

## Current top-level state

- Production logical lines: `208435`
- Engine logical lines: `6989`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `116`
- Registered runtime components: `101`
- Oversized production modules: `4`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
