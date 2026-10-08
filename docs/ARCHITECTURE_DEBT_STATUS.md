---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "24f770924688ab91a2da8877998e292eb0075306df3a68e5cf13ba310ecb79fd"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `24f770924688ab91a2da8877998e292eb0075306df3a68e5cf13ba310ecb79fd`

## Current top-level state

- Production logical lines: `241695`
- Engine logical lines: `6934`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `128`
- Registered runtime components: `121`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
