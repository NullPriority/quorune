---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "913b4b3eac3cb5a5865d3d78a5a9cd3a7318fc73a33d976d4cb62351da63dd4b"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `913b4b3eac3cb5a5865d3d78a5a9cd3a7318fc73a33d976d4cb62351da63dd4b`

## Current top-level state

- Production logical lines: `229056`
- Engine logical lines: `6951`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `123`
- Registered runtime components: `117`
- Oversized production modules: `7`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
