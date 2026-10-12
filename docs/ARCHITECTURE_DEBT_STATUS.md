---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "c44f9fce15363761757f69918398ffd9f260aa176b43b67a43c877262d9c992c"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `c44f9fce15363761757f69918398ffd9f260aa176b43b67a43c877262d9c992c`

## Current top-level state

- Production logical lines: `249964`
- Engine logical lines: `6874`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `130`
- Registered runtime components: `140`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
