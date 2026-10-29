---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "1fced4d89dcdc305e160e5dda2e40d54e101e4d79a8cbef7f41d49f309211877"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `1fced4d89dcdc305e160e5dda2e40d54e101e4d79a8cbef7f41d49f309211877`

## Current top-level state

- Production logical lines: `292258`
- Engine logical lines: `6616`
- Direct GameState-write heuristic: `74`
- Registered typed semantic handlers: `140`
- Registered runtime components: `247`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
