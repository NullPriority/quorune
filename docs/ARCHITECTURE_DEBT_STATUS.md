---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "0b43e7b695538ff9cb27ee427250ce71ff314047ddc341f30668ef291f655b92"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `0b43e7b695538ff9cb27ee427250ce71ff314047ddc341f30668ef291f655b92`

## Current top-level state

- Production logical lines: `246403`
- Engine logical lines: `6916`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `129`
- Registered runtime components: `130`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
