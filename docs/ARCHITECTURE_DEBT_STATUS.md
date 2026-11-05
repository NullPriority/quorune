---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "cfe7087703f5e7be3da3ca58f9a35a65d754f97360b156b14092bc6ed54e416b"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `cfe7087703f5e7be3da3ca58f9a35a65d754f97360b156b14092bc6ed54e416b`

## Current top-level state

- Production logical lines: `304642`
- Engine logical lines: `6450`
- Direct GameState-write heuristic: `74`
- Registered typed semantic handlers: `146`
- Registered runtime components: `253`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
