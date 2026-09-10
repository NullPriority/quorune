---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "e3998a5bc6b73b9c6accea554fba88d43b45b75f3472681c666641b737216eee"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `e3998a5bc6b73b9c6accea554fba88d43b45b75f3472681c666641b737216eee`

## Current top-level state

- Production logical lines: `210787`
- Engine logical lines: `6989`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `117`
- Registered runtime components: `101`
- Oversized production modules: `4`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
