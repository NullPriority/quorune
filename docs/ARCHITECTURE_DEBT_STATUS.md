---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "20f5eaf93dbedae0614260a6b2acbd27dfad620999a601d083bf967f3990328a"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `20f5eaf93dbedae0614260a6b2acbd27dfad620999a601d083bf967f3990328a`

## Current top-level state

- Production logical lines: `228431`
- Engine logical lines: `6951`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `123`
- Registered runtime components: `116`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
