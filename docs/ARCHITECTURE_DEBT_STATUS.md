---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "25c68a31b3f9b1891c4f8a6da13a8288f81161b18cbc909bf33a0eb780727aea"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `25c68a31b3f9b1891c4f8a6da13a8288f81161b18cbc909bf33a0eb780727aea`

## Current top-level state

- Production logical lines: `304771`
- Engine logical lines: `6450`
- Direct GameState-write heuristic: `74`
- Registered typed semantic handlers: `146`
- Registered runtime components: `253`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
