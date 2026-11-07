---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "e879bf2deab206fd76caf8366570981795a54122425a9782b8b86651e3b8e674"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `e879bf2deab206fd76caf8366570981795a54122425a9782b8b86651e3b8e674`

## Current top-level state

- Production logical lines: `315735`
- Engine logical lines: `6466`
- Direct GameState-write heuristic: `73`
- Registered typed semantic handlers: `156`
- Registered runtime components: `258`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
