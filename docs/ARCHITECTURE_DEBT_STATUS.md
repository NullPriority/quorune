---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "9ae09f13915b7fa4554bfe32b1b4745527509f860863a21afda51184dcdbe2bc"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `9ae09f13915b7fa4554bfe32b1b4745527509f860863a21afda51184dcdbe2bc`

## Current top-level state

- Production logical lines: `192136`
- Engine logical lines: `7027`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `115`
- Registered runtime components: `91`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
