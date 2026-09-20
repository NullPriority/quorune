---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "3975aa56ee3f6c9c5fadb0bf01a3833b9dce548cfa39f0f2378dd80b70bf72f9"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `3975aa56ee3f6c9c5fadb0bf01a3833b9dce548cfa39f0f2378dd80b70bf72f9`

## Current top-level state

- Production logical lines: `221854`
- Engine logical lines: `6986`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `120`
- Registered runtime components: `106`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
