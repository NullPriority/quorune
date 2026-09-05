---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "8d70cb5fda6e1a63ee68c13c970c8a459c3015478ab2e5aaf9b28b118cf7a842"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `8d70cb5fda6e1a63ee68c13c970c8a459c3015478ab2e5aaf9b28b118cf7a842`

## Current top-level state

- Production logical lines: `204180`
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
