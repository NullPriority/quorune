---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "f6cd3706ef473d533b354ec529390c6d6251a86a6fcd14820868e965180a3ffc"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `f6cd3706ef473d533b354ec529390c6d6251a86a6fcd14820868e965180a3ffc`

## Current top-level state

- Production logical lines: `328493`
- Engine logical lines: `6470`
- Direct GameState-write heuristic: `70`
- Registered typed semantic handlers: `159`
- Registered runtime components: `264`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
