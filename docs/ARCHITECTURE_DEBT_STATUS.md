---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "776e20898f1a6fd4962b2a0c90e3e79fffc88fbbdf8ac1335e7e775b22f62bbd"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `776e20898f1a6fd4962b2a0c90e3e79fffc88fbbdf8ac1335e7e775b22f62bbd`

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
