---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "5fae191c83f3ab2271fd7627e7bcd5591efbf3e322fc5fd63ba95b694f88ebc1"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `5fae191c83f3ab2271fd7627e7bcd5591efbf3e322fc5fd63ba95b694f88ebc1`

## Current top-level state

- Production logical lines: `241869`
- Engine logical lines: `6934`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `128`
- Registered runtime components: `122`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
