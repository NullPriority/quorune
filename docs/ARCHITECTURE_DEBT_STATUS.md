---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "a196e7aeea5d2eed888251663fdb501e81ef4bd72494f32b77c90f1dee70c386"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `a196e7aeea5d2eed888251663fdb501e81ef4bd72494f32b77c90f1dee70c386`

## Current top-level state

- Production logical lines: `256051`
- Engine logical lines: `6766`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `132`
- Registered runtime components: `157`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
