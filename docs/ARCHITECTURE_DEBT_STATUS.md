---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "09817d8833bff38a2bce3876fce3ac4a3686dd277916be7fe6c35a2d09c97761"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `09817d8833bff38a2bce3876fce3ac4a3686dd277916be7fe6c35a2d09c97761`

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
