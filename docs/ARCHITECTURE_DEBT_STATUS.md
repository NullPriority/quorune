---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "f9baaf5661570fda3eb44e7a40ad9443f12a837a418e45453576bb65a40c2dec"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `f9baaf5661570fda3eb44e7a40ad9443f12a837a418e45453576bb65a40c2dec`

## Current top-level state

- Production logical lines: `537329`
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
