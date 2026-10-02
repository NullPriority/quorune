---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "3e4490bed859ec535841266dd60904ecbd0559c9b051c1286cd113f9f5838c26"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `3e4490bed859ec535841266dd60904ecbd0559c9b051c1286cd113f9f5838c26`

## Current top-level state

- Production logical lines: `235745`
- Engine logical lines: `6950`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `124`
- Registered runtime components: `118`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
