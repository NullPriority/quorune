---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "d9f2e56370eaab4523d4706e10fcd32a59b9b428e1d6233b2b317ca97a50bf9f"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `d9f2e56370eaab4523d4706e10fcd32a59b9b428e1d6233b2b317ca97a50bf9f`

## Current top-level state

- Production logical lines: `186991`
- Engine logical lines: `6994`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `115`
- Registered runtime components: `90`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
