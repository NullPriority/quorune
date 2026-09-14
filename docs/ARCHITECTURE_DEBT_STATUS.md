---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "6e834e845426a674aecbe8c4aaac1e0376185596d299c7801677d6806432b170"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `6e834e845426a674aecbe8c4aaac1e0376185596d299c7801677d6806432b170`

## Current top-level state

- Production logical lines: `214540`
- Engine logical lines: `6987`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `119`
- Registered runtime components: `101`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
