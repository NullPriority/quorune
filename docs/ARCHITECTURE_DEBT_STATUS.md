---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "a1d53da7c70c11a95ab7dea9eb74e5964710a5c40a1f1d84477d4dacf921ad54"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `a1d53da7c70c11a95ab7dea9eb74e5964710a5c40a1f1d84477d4dacf921ad54`

## Current top-level state

- Production logical lines: `208435`
- Engine logical lines: `6989`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `116`
- Registered runtime components: `101`
- Oversized production modules: `4`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
