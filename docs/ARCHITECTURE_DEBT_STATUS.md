---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "e4d93c00dc52c4a666301d39ead4e3bdeb47c5b7addae511d0680596b638c9bc"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `e4d93c00dc52c4a666301d39ead4e3bdeb47c5b7addae511d0680596b638c9bc`

## Current top-level state

- Production logical lines: `337414`
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
