---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "c7ad4f8f513dced191f3551ec21fddd6cbf489d0f1e52f38361b56486bcd8173"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `c7ad4f8f513dced191f3551ec21fddd6cbf489d0f1e52f38361b56486bcd8173`

## Current top-level state

- Production logical lines: `204162`
- Engine logical lines: `7040`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `116`
- Registered runtime components: `101`
- Oversized production modules: `4`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
