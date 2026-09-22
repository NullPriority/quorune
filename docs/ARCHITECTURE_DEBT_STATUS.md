---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "4d9886dd56df2984883db6ada1f3e499eca22890d9cd62cad932331a40a0c4cf"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `4d9886dd56df2984883db6ada1f3e499eca22890d9cd62cad932331a40a0c4cf`

## Current top-level state

- Production logical lines: `226307`
- Engine logical lines: `6963`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `121`
- Registered runtime components: `114`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
