---
title: "Typed Oracle IR"
status: "current"
authoritative_source: "Oracle compiler implementation, CardProgram schema, and pinned corpus reports"
verified: "2026-09-16"
audience: "compiler, rules, and CardProgram contributors"
maintenance: "hand-maintained"
concern: "oracle-ir"
---

# Typed Oracle IR

Typed Oracle IR is the deterministic compiler representation between pinned
Oracle/rulings input and canonical CardProgram abilities. It classifies source
text without giving Oracle prose runtime authority. The compiler and
[CardProgram schema](../../schemas/card-program-v2.schema.json) define the exact
node shapes; generated [compiler status](../COMPILER_COVERAGE_STATUS.md) owns
current counts and residual inventories.

## Compiler result

For each Oracle face and material ability, compilation records:

- Oracle ID, face identity, stable ability key, and active zones;
- exact normalized source span and source-text hash;
- typed costs, timing, modes, targets, choices, effects, triggers, static
  descriptors, replacements, prevention, and continuations where recognized;
- fine-grained capability and runtime-component requirements;
- applicable Comprehensive Rules and ruling provenance;
- compiler/template identity and deterministic semantic fingerprint; and
- every unmatched material span as a classified residual.

The CardProgram groups these abilities with card-level source hashes,
provenance, trust basis, closure, residuals, and one deterministic artifact
fingerprint. An omitted family means the artifact does not declare it; it is not
evidence of universal support.

## Stages

```text
pinned Oracle face and rulings
        |
normalization and source spans
        |
declaration and clause parsing
        |
typed IR nodes + material residuals
        |
CardProgram lowering and validation
        |
capability, interaction, and profile closure
```

Normalization selects faces and preserves written instruction order. Parsing
recognizes closed grammar and retains exact spans. Lowering produces versioned
typed constructs. Trust validation checks the complete materially reachable
program; it never treats a syntactic match as implemented behavior.

## Status meanings

- `exact`: every material span is represented by validated typed constructs for
  the compiler claim.
- `partial`: at least one material span lowers, while another remains residual
  or a runtime dependency is unresolved.
- `unresolved`: no safe executable representation exists for material text.
- `intentionally_ignored`: the span is reviewed as immaterial to the declared
  operation/profile and carries explicit provenance.

Compiler exactness, CardProgram construction, capability closure, supported
profile closure, and trusted execution are separate claims. A card can parse
exactly while remaining unavailable because a target, replacement, layer,
copy, multiplayer, privacy, or runtime dependency is not trusted.

## Provenance and identity

Compilation is deterministic for the same Oracle/rulings snapshot, compiler,
templates, capability registry, and policy. Source hashes and spans distinguish
changed wording from an implementation change. Stable ability identity must be
unique within its card; ambiguous keys fail closed instead of selecting a node
by iteration order.

The complete CardProgram and trust fingerprints are pinned in new Game Records.
Historical records deserialize their pinned artifact. A current compiler
correction changes the relevant compiler/template and artifact fingerprints;
it does not rewrite an old record.

## Runtime boundary

`CommanderSession` registers validated generated and reviewed abilities under
stable identities. A reviewed compatibility ability may supersede only the same
key; conflicting face or source identity fails loading. Exact executable nodes
enter registered typed semantic handlers or runtime components. Unsupported or
untrusted nodes remain unavailable in strict play or reach an explicit
development-only arbitration boundary.

No running game parses Oracle prose. The runtime consumes only validated,
fingerprinted CardPrograms, registered descriptors, and typed rules owners.
Combat declaration costs, restrictions, and requirements are lowered as
closed static-ability fragments. Their registered handlers add the fragments
to the same effective layer-6 ability snapshot used by other static abilities;
the declaration solver filters that snapshot by typed fragment class and never
consults display text. Fixed public declaration conditions reuse the shared
public-state condition descriptor, while attached all-ability and nonmana
activation prohibitions remain typed current fragments rather than runtime
text. Source-controller restrictions retain their originating static source.

## Residual and override policy

A residual identifies the smallest unsupported material span, compiler stage,
reason, and expected dependency. Repeated wording gaps should become one generic
grammar production and reusable runtime family. Do not add a card name,
collector number, set code, or Oracle ID branch to the generic runtime.

A genuinely irreducible reviewed override is compiled data. It pins Oracle ID,
face/ability, Oracle and rulings hashes, residual classification, rules and
capabilities, authoring/review provenance, and deterministic assurance. See the
[override guide](../extension/card-override.md).

## Commands

```powershell
.\.venv\Scripts\python.exe simctl.py oracle parse "<card>" `
  --db data/scryfall-current.sqlite3
.\.venv\Scripts\python.exe simctl.py oracle explain "<card>" `
  --db data/scryfall-current.sqlite3
.\.venv\Scripts\python.exe simctl.py card compile "<card>" `
  --db data/scryfall-current.sqlite3
.\.venv\Scripts\python.exe simctl.py card audit "<card>" `
  --db data/scryfall-current.sqlite3
.\.venv\Scripts\python.exe simctl.py card coverage `
  --db data/scryfall-current.sqlite3
```

Use [compiler architecture](../architecture/compiler.md),
[CardProgram architecture](../architecture/card-programs.md), and
[rules assurance](../rules/assurance-model.md) when extending the pipeline.
