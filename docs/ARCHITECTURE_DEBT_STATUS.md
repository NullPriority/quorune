---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "b50d40cee5d2fadb436b00a8dcc4df46bcb5cc5862cec5c83c3c6968cdf6944a"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `b50d40cee5d2fadb436b00a8dcc4df46bcb5cc5862cec5c83c3c6968cdf6944a`

## Current top-level state

- Production logical lines: `247240`
- Engine logical lines: `6916`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `129`
- Registered runtime components: `131`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
