---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "5e833708ca0cf44d8db683edc39d55ba2634d0559e5e84b062518df6c85b1135"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `5e833708ca0cf44d8db683edc39d55ba2634d0559e5e84b062518df6c85b1135`

## Current top-level state

- Production logical lines: `177023`
- Engine logical lines: `7033`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `107`
- Registered runtime components: `86`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
