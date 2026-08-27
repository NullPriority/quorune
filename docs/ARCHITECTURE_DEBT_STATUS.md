---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "2eac6a7addfb5d698a5401368b3fe3af152cfe381d600f4b3dd4a44e2aad2535"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `2eac6a7addfb5d698a5401368b3fe3af152cfe381d600f4b3dd4a44e2aad2535`

## Current top-level state

- Production logical lines: `184211`
- Engine logical lines: `7065`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `115`
- Registered runtime components: `88`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
