---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "f849fa9b063b043c59c5ca9c34371cd555c8e03caff2e9d46cbba3a8e781b16b"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `f849fa9b063b043c59c5ca9c34371cd555c8e03caff2e9d46cbba3a8e781b16b`

## Current top-level state

- Production logical lines: `242050`
- Engine logical lines: `6934`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `128`
- Registered runtime components: `122`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
