---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "7aa15ad4352289cd9c5d34ae43a81c446fdfdeb49de99cf7ee7b3e68d280fb57"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `7aa15ad4352289cd9c5d34ae43a81c446fdfdeb49de99cf7ee7b3e68d280fb57`

## Current top-level state

- Production logical lines: `305404`
- Engine logical lines: `6453`
- Direct GameState-write heuristic: `73`
- Registered typed semantic handlers: `146`
- Registered runtime components: `253`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
