---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "447e228b7f626c43cbeac38b99eccba99daa024be98b453174fdf8a3288163bc"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `447e228b7f626c43cbeac38b99eccba99daa024be98b453174fdf8a3288163bc`

## Current top-level state

- Production logical lines: `343937`
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
