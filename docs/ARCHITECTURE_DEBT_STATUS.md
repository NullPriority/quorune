---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "f7cfe694c69a758f63844f806ec45defd0e77f95fd01923344f1a72e6a1dde1d"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `f7cfe694c69a758f63844f806ec45defd0e77f95fd01923344f1a72e6a1dde1d`

## Current top-level state

- Production logical lines: `258828`
- Engine logical lines: `6771`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `133`
- Registered runtime components: `164`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
