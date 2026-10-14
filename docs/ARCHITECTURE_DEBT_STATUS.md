---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "5237568b41a2c6c167151300291b5807aa7adcd6abb62eb9c3323c1c68a33a4c"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `5237568b41a2c6c167151300291b5807aa7adcd6abb62eb9c3323c1c68a33a4c`

## Current top-level state

- Production logical lines: `254672`
- Engine logical lines: `6853`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `132`
- Registered runtime components: `156`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
