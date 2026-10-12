---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "bedecfd4bc9ff2dd2a5af77baed39e13ed7d052a882d7df04991e4378833a2e2"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `bedecfd4bc9ff2dd2a5af77baed39e13ed7d052a882d7df04991e4378833a2e2`

## Current top-level state

- Production logical lines: `249938`
- Engine logical lines: `6874`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `130`
- Registered runtime components: `140`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
