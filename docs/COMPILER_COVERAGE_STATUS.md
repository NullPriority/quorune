---
title: "Compiler coverage status"
status: "generated"
authoritative_source: "coverage/architecture-audit.json"
verified: "033ce2c53a68ec838f893c132444f3bac81d756404ae22fab8b65869231dd0fe"
audience: "compiler and rules contributors"
maintenance: "generated"
generated_source: "coverage/architecture-audit.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write"
---

# Compiler coverage status

Source fingerprint: `033ce2c53a68ec838f893c132444f3bac81d756404ae22fab8b65869231dd0fe`

## Current top-level state

- Compiler version: `oracle-ir-v205`
- Runtime IR: `OracleCardIR lowered to canonical CardProgram V2 with a derived SemanticProgram compatibility index`
- CardProgram schema version: `2`
- Commander Oracle objects: `31623`
- Exact fraction: `0.338456`
- Capability records: `279`
- Assured fixed-target compiler nodes/shapes: `689` / `135`

## Top blockers

- The pinned Commander Oracle snapshot is not capability-complete.
- Material compiler residuals remain: `28829`.
- Blocked capability records remain: `4`.
- Configured evidence is incomplete for: `lexing`, `binding`.

Complete corpus, residual, stage, capability, and CardProgram inventories are in the [machine-readable architecture audit](../coverage/architecture-audit.json). The corpus-derived fixed-target grammar shapes and representative identities are in the [Commander Oracle census](../coverage/oracle-coverage-commander.json).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_architecture_audit.py --write
```
