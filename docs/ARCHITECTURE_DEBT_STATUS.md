---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "b451fbd433825f96f7baa5f31bd5ae436956377892069720fcbbd9ac973f09d9"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `b451fbd433825f96f7baa5f31bd5ae436956377892069720fcbbd9ac973f09d9`

## Current top-level state

- Production logical lines: `537299`
- Engine logical lines: `6441`
- Direct GameState-write heuristic: `67`
- Registered typed semantic handlers: `206`
- Registered runtime components: `424`
- Oversized production modules: `3`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
