---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "50ae3e4d98c1c3ebcd5707f92d9ee09df7137702e2ddba7494e3ae0802de42d5"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `50ae3e4d98c1c3ebcd5707f92d9ee09df7137702e2ddba7494e3ae0802de42d5`

## Current top-level state

- Production logical lines: `292728`
- Engine logical lines: `6538`
- Direct GameState-write heuristic: `74`
- Registered typed semantic handlers: `140`
- Registered runtime components: `247`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
