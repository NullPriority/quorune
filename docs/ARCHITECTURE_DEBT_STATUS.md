---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "fa0e7afdbca74941bc2812776b83d498f7b2e6654de9b66b9e941e122d5b4c36"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `fa0e7afdbca74941bc2812776b83d498f7b2e6654de9b66b9e941e122d5b4c36`

## Current top-level state

- Production logical lines: `305048`
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
