---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "7923d434445a7dfdda6e686823f8bf5a6897f1bf2c180318deddc7953cb083e9"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `7923d434445a7dfdda6e686823f8bf5a6897f1bf2c180318deddc7953cb083e9`

## Current top-level state

- Production logical lines: `191189`
- Engine logical lines: `6994`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `115`
- Registered runtime components: `91`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
