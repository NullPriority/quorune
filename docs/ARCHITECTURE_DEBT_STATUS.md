---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "664e1ff3a7b41d24c8e14020ff601703ef8753337bce4af1fe8032a00891370d"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `664e1ff3a7b41d24c8e14020ff601703ef8753337bce4af1fe8032a00891370d`

## Current top-level state

- Production logical lines: `315880`
- Engine logical lines: `6469`
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
