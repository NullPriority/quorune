---
title: "Documentation map and standard"
status: "current"
authoritative_source: "platform/documentation-policy.json and the maintained documentation set"
verified: "2026-08-08"
audience: "users, operators, contributors, and coding agents"
maintenance: "hand-maintained"
---

# Documentation map and standard

This page is the authoritative map for maintained project documentation.
Current prose explains stable contracts and links to generated evidence for
changing measurements. Accepted decisions remain in ADRs; release chronology
belongs in the changelog or `docs/history/`.

## Start here

- [Product overview and quick start](../README.md)
- [Browser product guide](product/browser.md)
- [Local application operations](operations/local-app.md)
- [Architecture portal](../ARCHITECTURE.md)
- [Contributor contract](../CONTRIBUTING.md)
- [Security policy](../SECURITY.md)
- [Code of Conduct](../CODE_OF_CONDUCT.md)
- [Agent instructions](../AGENTS.md)
- [Rebrand and compatibility status](REBRAND_STATUS.md)
- [Migrating existing checkouts to Quorune](MIGRATING_TO_QUORUNE.md)

## Product, operations, and protocol

- [Browser product guide](product/browser.md)
- [Local application operations](operations/local-app.md)
- [Hosted deployment boundary](operations/hosted.md)
- [Network protocol](reference/protocol.md)
- [Generated protocol inventory](reference/protocol-inventory.md)
- [Game Record contract](reference/game-record.md)
- [Oracle IR reference](reference/oracle-ir.md)
- [Local card database](../data/README.md)
- [Protocol smoke fixture](../demo/SMOKE_TEST.md)

## Architecture

- [System context](architecture/context.md)
- [Runtime containers](architecture/containers.md)
- [Rules kernel](architecture/rules-kernel.md)
- [CardProgram architecture](architecture/card-programs.md)
- [Oracle compiler architecture](architecture/compiler.md)
- [Typed semantic handlers](architecture/semantic-handlers.md)
- [Runtime components](architecture/runtime-components.md)
- [Reusable rules pieces](architecture/reusable-rules-pieces.md)
- [Counter placement](architecture/counter-placement.md)
- [Damage transaction](architecture/damage.md)
- [Damage prevention](architecture/prevention.md)
- [Drawing](architecture/drawing.md)
- [Library inspection and ordering](architecture/library-ordering.md)
- [Target legality and protection](architecture/target-legality.md)
- [Trust closure](architecture/trust-closure.md)
- [Server runtime](architecture/server-runtime.md)
- [Replay](architecture/replay.md)
- [Visibility](architecture/visibility.md)
- [Dependency and mutation rules](architecture/dependency-rules.md)
- [Architecture decisions](adr/index.md)

## Rules, compiler, and extension references

- [Rules assurance model](rules/assurance-model.md)
- [Current rules claim boundary](RULES_COMPLETENESS_STATUS.md)
- [Mechanic contracts](../mechanics/contracts/README.md)
- [Derived rules metadata](../rules/README.md)
- [Card override extension](extension/card-override.md)
- [Mechanic capability extension](extension/mechanic-capability.md)
- [Semantic node extension](extension/semantic-node.md)
- [Runtime component extension](extension/runtime-component.md)

## Optional clients

- [Provider adapters](optional-clients/providers.md)
- [Quorune Pilot Harness adapter](optional-clients/codex-arena.md)
- [Pilot Harness operational skill](../.agents/skills/quorune-pilot-harness/SKILL.md)

Optional clients consume the ordinary seat-projected protocol. They do not
define rules authority or become a production runtime dependency.

## Testing and development

- [Testing strategy](testing/strategy.md)
- [CI pipeline and local workflow](development/ci-pipeline.md)
- [Interaction coverage](testing/interaction-coverage.md)
- [Replay testing](testing/replay.md)
- [Privacy testing](testing/privacy.md)
- [Mutation testing](testing/mutation.md)

## Legal, privacy, and security

- [Legal and third-party content boundary](LEGAL_CONTENT_BOUNDARY.md)
- [Threat model](THREAT_MODEL.md)
- [Security policy](../SECURITY.md)
- [Software license](../LICENSE)

## Generated current status

- [Platform implementation status](PLATFORM_IMPLEMENTATION_STATUS.md)
- [Architecture debt status](ARCHITECTURE_DEBT_STATUS.md)
- [Compiler coverage status](COMPILER_COVERAGE_STATUS.md)
- [Rules dependency queue](RULES_DEPENDENCY_QUEUE.md)
- [Platform readiness](../coverage/platform-readiness.md)
- [Card-unlock frontier](../coverage/card-unlock-frontier.md)
- [Reusable rules-piece matrix](../coverage/reusable-piece-matrix.md)
- [Reusable rules-piece delta](../coverage/reusable-piece-delta.md)
- [Complex-card composition](../coverage/complex-card-composition.md)
- [Mechanics coverage](../coverage/mechanics-coverage.md)
- [Rules coverage](../coverage/rules-coverage.md)
- [Rules conformance coverage](../coverage/rules-conformance.md)
- [Rules delta](../coverage/rules-delta.md)
- [CI escape report](../coverage/ci-escape-report.md)
- [Compact CI card dependencies](../coverage/compact-ci-card-dependencies.md)

## Decision and compatibility records

- [Changelog](../CHANGELOG.md)
- [Architecture decision records](adr/index.md)
- [Generated-artifact finalization decision](adr/0042-canonical-generated-artifact-finalization.md)
- [Face-status timestamp compatibility](adr/0150-versioned-face-status-timestamps.md)
- [Semantic-pack compatibility](history/semantic-packs.md)

## Maintenance rules

- Prefer editing an existing owner over adding a document.
- Give each document one dominant purpose and one audience.
- Keep hand-maintained current guidance free of copied metrics and transient
  branches, pull requests, and workflow runs.
- Put changing inventories in machine-readable generated artifacts and link to
  them from concise dashboards.
- Link to an authority instead of repeating its contract in several places.
- Delete superseded guidance in the same change as its replacement.
- Add an ADR only for a durable decision with meaningful alternatives and
  consequences.
- Run `scripts/finalize_generated.py --write` before the final commit; it also
  validates documentation and generated freshness.
