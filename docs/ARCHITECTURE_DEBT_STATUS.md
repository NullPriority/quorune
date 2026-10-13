---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "2915ac2c1cfb0e59c4df91173a722ec5f74d55e222156a96fa22023b071a8244"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `2915ac2c1cfb0e59c4df91173a722ec5f74d55e222156a96fa22023b071a8244`

## Current top-level state

- Production logical lines: `251465`
- Engine logical lines: `6879`
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
