---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "fa5407d7ae20726c916bd9091b8c83e0e847a49b9fbe3c58fd572d7c552cd805"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `fa5407d7ae20726c916bd9091b8c83e0e847a49b9fbe3c58fd572d7c552cd805`

## Current top-level state

- Production logical lines: `237663`
- Engine logical lines: `6950`
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
