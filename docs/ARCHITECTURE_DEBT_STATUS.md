---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "f2fd511f67ec04881cf7f3d3c4ddc1ab29159b5e98189da311538bd4cd267731"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `f2fd511f67ec04881cf7f3d3c4ddc1ab29159b5e98189da311538bd4cd267731`

## Current top-level state

- Production logical lines: `247240`
- Engine logical lines: `6916`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `129`
- Registered runtime components: `131`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
