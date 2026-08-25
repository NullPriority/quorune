---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "80320d0b92827b7e80de630f156da8a529b756b9e5a684ea7c5de5e15915570e"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `80320d0b92827b7e80de630f156da8a529b756b9e5a684ea7c5de5e15915570e`

## Current top-level state

- Production logical lines: `180729`
- Engine logical lines: `7065`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `108`
- Registered runtime components: `88`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
