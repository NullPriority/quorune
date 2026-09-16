---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "e7278e5ac4a0873692d948b37752db909df8b2afbb2f5b62141c1cedf52a2ca0"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `e7278e5ac4a0873692d948b37752db909df8b2afbb2f5b62141c1cedf52a2ca0`

## Current top-level state

- Production logical lines: `220338`
- Engine logical lines: `6986`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `120`
- Registered runtime components: `106`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
