---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "790a4e7b12c0698633adff5fbb471bd4be04e38437f52fd365feb54f95e3c4d8"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `790a4e7b12c0698633adff5fbb471bd4be04e38437f52fd365feb54f95e3c4d8`

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
