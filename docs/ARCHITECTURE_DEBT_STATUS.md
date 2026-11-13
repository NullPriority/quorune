---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "323a92c16e5ddadb01601462dcd87962eeff647f3c157417a9e1cccf27109493"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `323a92c16e5ddadb01601462dcd87962eeff647f3c157417a9e1cccf27109493`

## Current top-level state

- Production logical lines: `337472`
- Engine logical lines: `6458`
- Direct GameState-write heuristic: `70`
- Registered typed semantic handlers: `161`
- Registered runtime components: `279`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
