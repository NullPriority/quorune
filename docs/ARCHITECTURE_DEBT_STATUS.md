---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "1149d3a66db11874f2926743c173a5411caf2472ebae572dfd8a8bbfaa0aeb7a"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `1149d3a66db11874f2926743c173a5411caf2472ebae572dfd8a8bbfaa0aeb7a`

## Current top-level state

- Production logical lines: `237946`
- Engine logical lines: `6950`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `126`
- Registered runtime components: `118`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
