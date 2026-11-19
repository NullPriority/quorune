---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "b9eafa6fe20ed7e443973fed383e5fd63ef81eadc8092f88af73a183cb85202f"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `b9eafa6fe20ed7e443973fed383e5fd63ef81eadc8092f88af73a183cb85202f`

## Current top-level state

- Production logical lines: `344018`
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
