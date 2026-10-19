---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "6b3a813eb311822845607b607871d407bf485c495f5fb9a79d58dbff6927bd8a"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `6b3a813eb311822845607b607871d407bf485c495f5fb9a79d58dbff6927bd8a`

## Current top-level state

- Production logical lines: `267345`
- Engine logical lines: `6778`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `133`
- Registered runtime components: `194`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
