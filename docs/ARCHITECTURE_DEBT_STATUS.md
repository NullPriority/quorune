---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "017408d2f6f52e18b7fd6d30ae25118bc1a801bff10dda9c3f47f7d7f5dae2db"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `017408d2f6f52e18b7fd6d30ae25118bc1a801bff10dda9c3f47f7d7f5dae2db`

## Current top-level state

- Production logical lines: `367166`
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
