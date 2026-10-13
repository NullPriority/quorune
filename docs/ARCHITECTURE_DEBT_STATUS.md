---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "c3e12f06ea7c3f3ba841e92d731086c4a713aa473f8117945d3878ec247b5911"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `c3e12f06ea7c3f3ba841e92d731086c4a713aa473f8117945d3878ec247b5911`

## Current top-level state

- Production logical lines: `251480`
- Engine logical lines: `6853`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `131`
- Registered runtime components: `144`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
