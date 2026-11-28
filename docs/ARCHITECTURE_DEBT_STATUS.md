---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "8f9d3f7c4e32b5f52da28ce717b07dcf3642cacd4b6679b26950d5c20ea22058"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `8f9d3f7c4e32b5f52da28ce717b07dcf3642cacd4b6679b26950d5c20ea22058`

## Current top-level state

- Production logical lines: `537334`
- Engine logical lines: `6441`
- Direct GameState-write heuristic: `67`
- Registered typed semantic handlers: `206`
- Registered runtime components: `424`
- Oversized production modules: `3`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
