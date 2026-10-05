---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "19f554dc8ef2f1699b7b6889ed558c2104a25d5e8eb4c546ab3c8fcdc083033b"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `19f554dc8ef2f1699b7b6889ed558c2104a25d5e8eb4c546ab3c8fcdc083033b`

## Current top-level state

- Production logical lines: `239116`
- Engine logical lines: `6937`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `126`
- Registered runtime components: `118`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
