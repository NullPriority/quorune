---
title: "Compiler coverage status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "ade40b6478460963c91b0ccae19f1bbecdcbcff20c32263954f0cea7e8590ee8"
audience: "compiler and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Compiler coverage status

Source fingerprint: `ade40b6478460963c91b0ccae19f1bbecdcbcff20c32263954f0cea7e8590ee8`

## Current top-level state

- Compiler version: `oracle-ir-v852`
- Runtime IR: `OracleCardIR lowered to canonical CardProgram V2 with a derived SemanticProgram compatibility index`
- CardProgram schema version: `2`
- Commander Oracle objects: `31623`
- Exact fraction: `0.553774`
- Capability records: `726`
- Assured fixed-target compiler nodes/shapes: `1014` / `281`

## Top blockers

- The pinned Commander Oracle snapshot is not capability-complete.
- Material compiler residuals remain: `18731`.
- Blocked capability records remain: `4`.
- Configured evidence is incomplete for: `lexing`, `binding`.

Complete corpus, residual, stage, capability, and CardProgram inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json). The corpus-derived fixed-target grammar shapes and representative identities are in the [Commander Oracle census](../coverage/oracle-coverage-commander.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
