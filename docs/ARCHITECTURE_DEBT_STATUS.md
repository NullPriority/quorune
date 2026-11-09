---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "f4f359e0f38bf820f9ca069359a8684edd6777fe67a281f712157131bbb53dcc"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `f4f359e0f38bf820f9ca069359a8684edd6777fe67a281f712157131bbb53dcc`

## Current top-level state

- Production logical lines: `328097`
- Engine logical lines: `6470`
- Direct GameState-write heuristic: `70`
- Registered typed semantic handlers: `159`
- Registered runtime components: `264`
- Oversized production modules: `5`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
