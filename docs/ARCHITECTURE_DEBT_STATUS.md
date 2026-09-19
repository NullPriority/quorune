---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "28860430fd92633d81411eb1ec2c587cfeaa8e6156304ae8e33d3aee1bd36bfa"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `28860430fd92633d81411eb1ec2c587cfeaa8e6156304ae8e33d3aee1bd36bfa`

## Current top-level state

- Production logical lines: `221827`
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
