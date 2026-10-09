---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "b51aff0be08ef42f639f932ce3efefd38a9a6d2b27323410cebd06674eccbd0a"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `b51aff0be08ef42f639f932ce3efefd38a9a6d2b27323410cebd06674eccbd0a`

## Current top-level state

- Production logical lines: `242951`
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
