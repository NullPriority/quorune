---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "21296ba295939dcc14b9fc671f296bbc56f6857985c391162e5bee302d2ca270"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `21296ba295939dcc14b9fc671f296bbc56f6857985c391162e5bee302d2ca270`

## Current top-level state

- Production logical lines: `304128`
- Engine logical lines: `6468`
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
