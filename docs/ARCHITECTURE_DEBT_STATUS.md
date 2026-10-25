---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "033b62544d5b9b8818fb80ac0448a6dde4ddc67fdc8840ab42c12d55b8c839ee"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `033b62544d5b9b8818fb80ac0448a6dde4ddc67fdc8840ab42c12d55b8c839ee`

## Current top-level state

- Production logical lines: `282608`
- Engine logical lines: `6610`
- Direct GameState-write heuristic: `74`
- Registered typed semantic handlers: `139`
- Registered runtime components: `226`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
