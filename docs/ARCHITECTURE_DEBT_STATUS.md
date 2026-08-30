---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "74b5cf42264cd6af28a6111041c3390ed18070667c232304229883b0e9e00a83"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `74b5cf42264cd6af28a6111041c3390ed18070667c232304229883b0e9e00a83`

## Current top-level state

- Production logical lines: `190036`
- Engine logical lines: `6994`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `115`
- Registered runtime components: `90`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
