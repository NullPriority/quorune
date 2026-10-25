---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "c8d3f3c4e3aa92607274fb69d2bcca4e375629904f732dc84fbdd0c004b75163"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `c8d3f3c4e3aa92607274fb69d2bcca4e375629904f732dc84fbdd0c004b75163`

## Current top-level state

- Production logical lines: `282683`
- Engine logical lines: `6610`
- Direct GameState-write heuristic: `74`
- Registered typed semantic handlers: `139`
- Registered runtime components: `226`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
