---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "a48043f86b42939999c21cbe5cd113a6dbb22957147adbcf2392644db836a8d9"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `a48043f86b42939999c21cbe5cd113a6dbb22957147adbcf2392644db836a8d9`

## Current top-level state

- Production logical lines: `181676`
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
