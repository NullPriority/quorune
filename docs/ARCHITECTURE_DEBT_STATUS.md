---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "c5a7c30e5371dc9d5db9c3600fe5442430eec0aec96ca187b44837eacbc7fa0c"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `c5a7c30e5371dc9d5db9c3600fe5442430eec0aec96ca187b44837eacbc7fa0c`

## Current top-level state

- Production logical lines: `258858`
- Engine logical lines: `6771`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `133`
- Registered runtime components: `164`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
