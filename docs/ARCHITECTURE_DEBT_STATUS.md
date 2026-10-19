---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "1a579870b531a39d573d6bbc34a00afa9e40e87e5436499211b5f19264821b42"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `1a579870b531a39d573d6bbc34a00afa9e40e87e5436499211b5f19264821b42`

## Current top-level state

- Production logical lines: `267350`
- Engine logical lines: `6778`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `133`
- Registered runtime components: `194`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
