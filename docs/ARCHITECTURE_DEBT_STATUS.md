---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "a92dc694f55e6a9cf72c0d16b455fd680b165456ae02aa061505b5f310d03337"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `a92dc694f55e6a9cf72c0d16b455fd680b165456ae02aa061505b5f310d03337`

## Current top-level state

- Production logical lines: `292728`
- Engine logical lines: `6538`
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
