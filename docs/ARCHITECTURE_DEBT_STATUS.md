---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "de3dd9aec10ccdb79578cf9c79a6036d219cf7d7f32bf3017c8ef706144de08f"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `de3dd9aec10ccdb79578cf9c79a6036d219cf7d7f32bf3017c8ef706144de08f`

## Current top-level state

- Production logical lines: `343931`
- Engine logical lines: `6459`
- Direct GameState-write heuristic: `70`
- Registered typed semantic handlers: `167`
- Registered runtime components: `283`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
