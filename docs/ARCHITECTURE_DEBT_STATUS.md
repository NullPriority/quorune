---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "b67b29a3d1cafc3cbdd7df61413ca56ca1664fc2299aee239969a0b5dc4f179c"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `b67b29a3d1cafc3cbdd7df61413ca56ca1664fc2299aee239969a0b5dc4f179c`

## Current top-level state

- Production logical lines: `210054`
- Engine logical lines: `6989`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `117`
- Registered runtime components: `101`
- Oversized production modules: `4`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
