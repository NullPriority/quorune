---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "7cf4e3d581c9dca061b1c54c878fac0cd76fc9e1718d35971696ebd0c01d894d"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `7cf4e3d581c9dca061b1c54c878fac0cd76fc9e1718d35971696ebd0c01d894d`

## Current top-level state

- Production logical lines: `230498`
- Engine logical lines: `6946`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `123`
- Registered runtime components: `117`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
