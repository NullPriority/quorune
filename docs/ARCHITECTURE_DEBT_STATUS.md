---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "b653819afeb3f47a21458e4cff0e1ffac3346d5af289b67435f4cc773dfe970a"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `b653819afeb3f47a21458e4cff0e1ffac3346d5af289b67435f4cc773dfe970a`

## Current top-level state

- Production logical lines: `246401`
- Engine logical lines: `6916`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `129`
- Registered runtime components: `130`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
