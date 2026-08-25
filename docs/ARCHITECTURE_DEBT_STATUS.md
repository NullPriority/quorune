---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "373188d5b695e6feff2458a19113899541f35cf591777d9c94f1f31d1454f13f"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `373188d5b695e6feff2458a19113899541f35cf591777d9c94f1f31d1454f13f`

## Current top-level state

- Production logical lines: `179412`
- Engine logical lines: `7064`
- Direct GameState-write heuristic: `82`
- Registered typed semantic handlers: `107`
- Registered runtime components: `88`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
