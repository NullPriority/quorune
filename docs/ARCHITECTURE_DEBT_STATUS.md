---
title: "Architecture debt status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "fc11f9119e2e1d87f742ae8bfe1dd59c9d3d507cd9535ff6e31be903f6f8fd10"
audience: "maintainers and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Architecture debt status

Source fingerprint: `fc11f9119e2e1d87f742ae8bfe1dd59c9d3d507cd9535ff6e31be903f6f8fd10`

## Current top-level state

- Production logical lines: `208406`
- Engine logical lines: `7004`
- Direct GameState-write heuristic: `77`
- Registered typed semantic handlers: `116`
- Registered runtime components: `101`
- Oversized production modules: `4`

## Top blockers

- None detected by the configured architecture policy.

Complete module, symbol, ownership, test, and documentation inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
