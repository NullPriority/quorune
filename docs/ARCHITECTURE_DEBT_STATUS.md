---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "c4c147f1559e52f942806d7886047a77b83ff77f4ed0ac7f70d19f4f722e51e8"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `c4c147f1559e52f942806d7886047a77b83ff77f4ed0ac7f70d19f4f722e51e8`

## Current top-level state

- Production logical lines: `202143`
- Engine logical lines: `7028`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `116`
- Registered runtime components: `98`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
