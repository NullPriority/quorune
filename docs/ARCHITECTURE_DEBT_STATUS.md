---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "3914cfc607d7d9c5693e49e9d90a58741903163dfde6ac1500df21315e42a6cc"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `3914cfc607d7d9c5693e49e9d90a58741903163dfde6ac1500df21315e42a6cc`

## Current top-level state

- Production logical lines: `249124`
- Engine logical lines: `6874`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `130`
- Registered runtime components: `139`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
