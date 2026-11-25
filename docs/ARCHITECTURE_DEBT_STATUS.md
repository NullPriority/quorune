---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "cd9c45ce4951a726ccc4fb93503e25377672fbb096f71b6125c9ce91c6e42fbf"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `cd9c45ce4951a726ccc4fb93503e25377672fbb096f71b6125c9ce91c6e42fbf`

## Current top-level state

- Production logical lines: `406899`
- Engine logical lines: `6461`
- Direct GameState-write heuristic: `71`
- Registered typed semantic handlers: `184`
- Registered runtime components: `343`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
