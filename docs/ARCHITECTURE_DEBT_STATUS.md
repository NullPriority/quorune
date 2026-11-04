---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "95be7f5e73e6ffc25adf0a979475388db2bedbd92cbd18ce859720e95a6057fa"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `95be7f5e73e6ffc25adf0a979475388db2bedbd92cbd18ce859720e95a6057fa`

## Current top-level state

- Production logical lines: `304113`
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
