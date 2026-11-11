---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "eb8bd7b01acab8705a5db9d53c5f067d574d6b81faa6c1580796293b7baa7285"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `eb8bd7b01acab8705a5db9d53c5f067d574d6b81faa6c1580796293b7baa7285`

## Current top-level state

- Production logical lines: `328498`
- Engine logical lines: `6470`
- Direct GameState-write heuristic: `70`
- Registered typed semantic handlers: `159`
- Registered runtime components: `264`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
