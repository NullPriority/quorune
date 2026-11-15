---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "a352533ea7c81fb3c8acb2641c8707d105f0af8e7cba0b13ed5329ce31af79e7"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `a352533ea7c81fb3c8acb2641c8707d105f0af8e7cba0b13ed5329ce31af79e7`

## Current top-level state

- Production logical lines: `343886`
- Engine logical lines: `6458`
- Direct GameState-write heuristic: `70`
- Registered typed semantic handlers: `167`
- Registered runtime components: `283`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
