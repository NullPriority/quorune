---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "9f95cd9f50090c1a3a7089db6b37ff098cf3e1e0f65b3265088c55a26c686dc4"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `9f95cd9f50090c1a3a7089db6b37ff098cf3e1e0f65b3265088c55a26c686dc4`

## Current top-level state

- Production logical lines: `366826`
- Engine logical lines: `6461`
- Direct GameState-write heuristic: `71`
- Registered typed semantic handlers: `175`
- Registered runtime components: `291`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
