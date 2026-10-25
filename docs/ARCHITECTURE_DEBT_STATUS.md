---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "e683280e5a838e644591a0d1e0a6f2bb790bc59bc8b38b23f82d8cd62042e871"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `e683280e5a838e644591a0d1e0a6f2bb790bc59bc8b38b23f82d8cd62042e871`

## Current top-level state

- Production logical lines: `282608`
- Engine logical lines: `6610`
- Direct GameState-write heuristic: `74`
- Registered typed semantic handlers: `139`
- Registered runtime components: `226`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
