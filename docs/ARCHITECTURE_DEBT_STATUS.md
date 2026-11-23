---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "a5605ad2a070efe996b9984a4974ae6088ab2ec0933e8ef0f4db796436c9cd19"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `a5605ad2a070efe996b9984a4974ae6088ab2ec0933e8ef0f4db796436c9cd19`

## Current top-level state

- Production logical lines: `367070`
- Engine logical lines: `6459`
- Direct GameState-write heuristic: `71`
- Registered typed semantic handlers: `175`
- Registered runtime components: `291`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
