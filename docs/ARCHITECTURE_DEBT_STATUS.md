---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "47113ccf2a82815d05363fa6e4a27529559c64e44c2c6293b625ddfeebf2672b"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `47113ccf2a82815d05363fa6e4a27529559c64e44c2c6293b625ddfeebf2672b`

## Current top-level state

- Production logical lines: `231508`
- Engine logical lines: `6948`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `123`
- Registered runtime components: `118`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
