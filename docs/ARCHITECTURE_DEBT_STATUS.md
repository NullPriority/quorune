---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "6196c5813c37df5146453c64053549e220cd92139bb6763dae984d8c0a241040"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `6196c5813c37df5146453c64053549e220cd92139bb6763dae984d8c0a241040`

## Current top-level state

- Production logical lines: `235231`
- Engine logical lines: `6950`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `124`
- Registered runtime components: `118`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
