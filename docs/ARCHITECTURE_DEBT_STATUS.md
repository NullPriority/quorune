---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "c15cbe4b805a60a72605ce1d8daab3e5b76c42175ba442c8bbb0ea7ac85fd9fc"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `c15cbe4b805a60a72605ce1d8daab3e5b76c42175ba442c8bbb0ea7ac85fd9fc`

## Current top-level state

- Production logical lines: `292591`
- Engine logical lines: `6547`
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
