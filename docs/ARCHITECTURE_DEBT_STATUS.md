---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "be65c2a34e7c40c1f9b4a567f6ebdbaf0f9354c25a511fc1b60cbd1255036ca7"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `be65c2a34e7c40c1f9b4a567f6ebdbaf0f9354c25a511fc1b60cbd1255036ca7`

## Current top-level state

- Production logical lines: `275473`
- Engine logical lines: `6725`
- Direct GameState-write heuristic: `74`
- Registered typed semantic handlers: `138`
- Registered runtime components: `208`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
