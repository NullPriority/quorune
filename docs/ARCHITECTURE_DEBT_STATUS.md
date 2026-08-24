---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "d7ef8e548652fec82cc3b940abfc057383ab0fc337852d705f14565b75714781"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `d7ef8e548652fec82cc3b940abfc057383ab0fc337852d705f14565b75714781`

## Current top-level state

- Production logical lines: `178108`
- Engine logical lines: `7033`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `107`
- Registered runtime components: `86`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
