---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "5ec76c3d9b861804795ee659facbdcac8625d11360bb2288c0fd73e0b953fee2"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `5ec76c3d9b861804795ee659facbdcac8625d11360bb2288c0fd73e0b953fee2`

## Current top-level state

- Production logical lines: `256051`
- Engine logical lines: `6766`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `132`
- Registered runtime components: `157`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
