---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "a3c366d4a63f2fbe4854f90a605196e44a0d9f94856bd402b529511ccc6f7e8d"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `a3c366d4a63f2fbe4854f90a605196e44a0d9f94856bd402b529511ccc6f7e8d`

## Current top-level state

- Production logical lines: `221480`
- Engine logical lines: `6986`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `120`
- Registered runtime components: `106`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
