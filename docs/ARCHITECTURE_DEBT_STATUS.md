---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "ade40b6478460963c91b0ccae19f1bbecdcbcff20c32263954f0cea7e8590ee8"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `ade40b6478460963c91b0ccae19f1bbecdcbcff20c32263954f0cea7e8590ee8`

## Current top-level state

- Production logical lines: `316095`
- Engine logical lines: `6452`
- Direct GameState-write heuristic: `73`
- Registered typed semantic handlers: `156`
- Registered runtime components: `258`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
