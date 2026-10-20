---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "c410d87050efd048d85685b408ccd496eaaaeed9f2b18f381a2274db053d6c0d"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `c410d87050efd048d85685b408ccd496eaaaeed9f2b18f381a2274db053d6c0d`

## Current top-level state

- Production logical lines: `271586`
- Engine logical lines: `6779`
- Direct GameState-write heuristic: `74`
- Registered typed semantic handlers: `134`
- Registered runtime components: `199`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
