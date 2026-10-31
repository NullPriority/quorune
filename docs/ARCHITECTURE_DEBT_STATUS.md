---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "77dc4e0d9b34cdbf1af61ef984da8a8359182c74e3847087e77bee1e357abd4b"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `77dc4e0d9b34cdbf1af61ef984da8a8359182c74e3847087e77bee1e357abd4b`

## Current top-level state

- Production logical lines: `292554`
- Engine logical lines: `6547`
- Direct GameState-write heuristic: `74`
- Registered typed semantic handlers: `140`
- Registered runtime components: `247`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
