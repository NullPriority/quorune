---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "7b39f4f1a0f423c5e8520cf3a7831af9592bbe7261c1cba45431831dce065bb5"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `7b39f4f1a0f423c5e8520cf3a7831af9592bbe7261c1cba45431831dce065bb5`

## Current top-level state

- Production logical lines: `404115`
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
