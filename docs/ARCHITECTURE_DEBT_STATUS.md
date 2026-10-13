---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "6fe4627d06036224bcd0733286c072babb7bd10fc2c7f23d2c1aa0913262e7e8"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `6fe4627d06036224bcd0733286c072babb7bd10fc2c7f23d2c1aa0913262e7e8`

## Current top-level state

- Production logical lines: `251488`
- Engine logical lines: `6853`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `131`
- Registered runtime components: `144`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
