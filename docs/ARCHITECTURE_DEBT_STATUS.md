---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "757bfd0b743168b2980ec6ff3d454eac1f01d2b7adc2633a83b117fb7dc06638"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `757bfd0b743168b2980ec6ff3d454eac1f01d2b7adc2633a83b117fb7dc06638`

## Current top-level state

- Production logical lines: `235349`
- Engine logical lines: `6950`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `124`
- Registered runtime components: `118`
- Oversized production modules: `6`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
