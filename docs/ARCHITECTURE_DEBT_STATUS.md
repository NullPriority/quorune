---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "84bc25baa34efb6bfe18bacf76f66abbf108e8d1b954cf9e63113b93c482d4fe"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `84bc25baa34efb6bfe18bacf76f66abbf108e8d1b954cf9e63113b93c482d4fe`

## Current top-level state

- Production logical lines: `183266`
- Engine logical lines: `7065`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `108`
- Registered runtime components: `88`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
