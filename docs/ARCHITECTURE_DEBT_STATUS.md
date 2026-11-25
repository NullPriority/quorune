---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "f98fe44a147448ccb189754f13196c7be903bc8477c70ca36d2ce57fbcaa43b5"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `f98fe44a147448ccb189754f13196c7be903bc8477c70ca36d2ce57fbcaa43b5`

## Current top-level state

- Production logical lines: `406899`
- Engine logical lines: `6461`
- Direct GameState-write heuristic: `71`
- Registered typed semantic handlers: `184`
- Registered runtime components: `343`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
