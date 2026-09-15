---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "bc61cd16d0b402ae99cfa024dd7467ea02507ec0ab1b968d3c7b6c6c2e824669"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `bc61cd16d0b402ae99cfa024dd7467ea02507ec0ab1b968d3c7b6c6c2e824669`

## Current top-level state

- Production logical lines: `217288`
- Engine logical lines: `6987`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `119`
- Registered runtime components: `103`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
