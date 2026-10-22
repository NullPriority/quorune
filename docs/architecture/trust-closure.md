---
title: "CardProgram trust and applicable closure"
status: "current"
authoritative_source: "quorune/card_programs/trust.py, binding.py, preflight.py, and the versioned capability registry"
verified: "2026-08-05"
audience: "rules, compiler, runtime, replay, and room-policy contributors"
maintenance: "hand-maintained"
---

# CardProgram trust and applicable closure

Trust is an executable, fingerprinted contract rather than one broad boolean.
The trust code owns no game state and performs no mutation. It consumes pinned
CardPrograms, the capability registry/evidence index, the selected profile,
registered handlers/components, and any dynamic capabilities; it produces
deterministic intrinsic, format, match, and dynamic closure reports.

## Trust bases

Every CardProgram V2 reports exactly one basis:

- `capability_closed`: every represented material ability has a current trusted
  intrinsic capability closure and no hidden compatibility-only path;
- `legacy_reviewed`: execution relies on source-pinned reviewed semantic-pack
  behavior whose fine-grained closure is incomplete;
- `mixed`: both bases occur in one card;
- `provisional`: behavior exists but its assurance is incomplete;
- `unresolved`: a material residual, stale source, arbiter dependency, or
  blocked capability remains;
- `non_rules_governed`: the represented text is intentionally outside the
  deterministic traditional-rules profile.

Legacy-reviewed behavior can keep the declared pool operational through the
explicit compatibility policy. It is never reported as capability-closed.
Compatibility provenance pins the source hashes, stable card/ability and
handler/component identities, tests, replay identity, and removal condition.

Preflight recognizes current generated static-handler coverage alongside
reviewed static coverage only for trusted, source-hash-matched programs.
Provisional or source-drifted handlers cannot close a static remainder.
Compatibility classification retains its separate intrinsic and format-layer
blockers; a generated handler label alone does not establish strict readiness.

## Closure layers

The intrinsic layer is derived from the card's costs, targets, effects, event
handlers, and runtime components. The format layer represents capabilities
always active for the selected rules profile. Match closure conservatively
unions all loaded programs and known format dependencies. Dynamic closure
binds capabilities introduced during play.

The current fine-grained registry does not yet inventory the complete
traditional or Commander format layer. Consequently capability-only strict
match readiness reports
`format_profile:capability_inventory_incomplete:<profile>`. Reviewed
compatibility remains separately available. This fail-closed blocker is
intentional; an empty format declaration is not proof that CR or Commander
ambient rules are complete.

Deck-level strict closure also reports every missing CardProgram, legacy or
mixed trust basis, and unbound per-card runtime dependency. The bounded
Commander match-readiness reference set keeps those categories separate from
format inventory and from reviewed-compatible execution; removing one category
does not silently promote the match while another remains.

Unrelated blocked registry entries do not block a match. A blocked capability
does block when it enters the match or dynamic reachable set. Conservative
overblocking is allowed; underblocking is not.

## Runtime binding and replay

Strict binding compares every declared ability closure to the current registry
and evidence fingerprints, then adds capability dependencies declared by each
registered semantic handler and runtime component. Missing declarations,
handler schema/event drift, untrusted closure, or a fingerprint mismatch fail
closed. The global inventories are metadata aggregations; family registries
remain the execution owners.

An activation-catalogue carrier with no resolution effects is structural
discovery metadata, not a hidden legacy rules program. Preflight may recognize a printed Equip through its source-matching trusted keyword owner on the exact parsed ability line. The fixed price, target restriction and sorcery timing must agree; provisional discovery catalogs do not establish coverage. Source drift and missing keyword capability retain the residual. Preflight also uses the canonical activation-cost specializer for its normalized printed abilities. Complete keyword-only static lines are covered only by their trusted printed keyword owner; unrelated static siblings and unknown cost symbols keep their residuals. Binding validates its
registered closed descriptor and schema/event identity without inventing a
behavioral capability closure. Any attached resolution effect still requires
its actual registered capabilities; a catalogue identity cannot excuse it.
Announced activation X additionally requires the existing complete-card
admission certificate at discovery. The compiler carries that certificate
through catalog attachment; missing certificates, unknown bodies and partial
siblings do not expose a paid X activation. Executable X catalogs separately
declare their ordinary activation-X capability dependency.

New Game Record v3 manifests pin capability registry/evidence, semantic-handler,
runtime-component, CardProgram, and CardProgram-trust fingerprints. A command
that resolves a semantic program also records a compact binding fingerprint and
the handler/component IDs used. Exact replay recomputes and compares them.
Historic v3 records without these additive fields retain their prior semantic
fingerprint verification.

Primary tests are `test_card_program_trust.py`, `test_preflight_v080.py`,
`test_game_record_v3.py`, and `test_semantic_searches.py`. Capability evidence
and mutation declarations are checked independently; see the
[capability guide](../extension/mechanic-capability.md),
[runtime-component architecture](runtime-components.md), and
[mutation testing guide](../testing/mutation.md).

Trust computation is deterministic metadata work. It is performed at compile,
preflight, load, and replay boundaries rather than inside characteristic hot
paths.

Terminal spell-self exile retains separate body and destination authority.
The stored marker, source context, template wrapper and exile destination must
agree, and the runtime binder independently requires the resolving-spell exit
capability. Removing its declaration or changing those fields rejects strict
admission. Existing price owners validate any surrounding additional cost
before the independently closed body is promoted.

The dynamic requirement reader validates typed `cast_x_effect_amount` values
across effects, handlers, target and cost data. Each requires
`quantity_expression.declared_effect_amount` independently of its surrounding
operation. Removing that annotation or supplying a malformed amount cannot
retain strict runtime trust. The expression still reads the canonical stack
declaration; it introduces no new expression registry or gameplay state.

Canonical public characteristic-set descriptors also require their resolution,
recipient-query, ordinary-keyword and typed quantity owners independently of
the program's annotations. The runtime binder decodes each stat amount and
validates the projected fixed set. Removing an owner, adding an unknown field,
or supplying an invalid quantity rejects strict admission before mutation.

The persisted `closed-effect-program` kind also requires its ordered-program
capability independently of the component owners and retains its bounded
effect count. Removing that capability or leaving a malformed single-effect
ordered program cannot retain strict runtime admission.
Typed public-query and scalar result values require their own quantity owner
throughout effects, handlers, target and cost data. Explicit instruction
bindings additionally require declared-amount authority. These requirements
come from decoding the stored value, including its schema and binding identity,
and do not depend on its surrounding operation or declared capability list.
Validated public-quantity mana descriptors delegate their nested producer to
the mana-production owner. Their codec forbids result declaration bindings and
scalar arithmetic; a similar-looking result value cannot use that delegation.

Session creation and one synchronous Game Record replay share one validated
read-only capability registry/evidence snapshot across their nested trust
checks. This is an invocation boundary: the next call re-reads and validates
registry, evidence, and implementation inputs, including edits with unchanged
file metadata. Nested component batches reuse the current snapshot; normal
return and exceptions clear it. No process-wide metadata cache grants trust,
and a session load that needs no capability validation remains lazy.
