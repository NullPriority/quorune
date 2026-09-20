---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "46fb02fba7d1ed6a22e1e0a41f52ff371c04adc6846fe34a3805330e5fb1836f"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `46fb02fba7d1ed6a22e1e0a41f52ff371c04adc6846fe34a3805330e5fb1836f`

## Current top-level state

- Production logical lines: `221914`
- Engine logical lines: `6986`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `120`
- Registered runtime components: `106`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
