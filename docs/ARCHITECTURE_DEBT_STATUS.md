---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "c928eeec013cefaa50aa10ff14f53b408cb2f6038464c06328164a5de4c748fb"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `c928eeec013cefaa50aa10ff14f53b408cb2f6038464c06328164a5de4c748fb`

## Current top-level state

- Production logical lines: `253517`
- Engine logical lines: `6853`
- Direct GameState-write heuristic: `75`
- Registered typed semantic handlers: `132`
- Registered runtime components: `147`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
