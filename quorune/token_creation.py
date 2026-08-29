from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .aura import (
    AuraEntryChoiceRequired,
    AuraEntryOutcome,
    AuraRuleError,
    commit_aura_entry_attachment,
    is_aura_type_line,
    prepare_aura_entry,
)
from .card_overrides import normalize_game_record_v3_runtime_handler
from .model import CardInstance
from .control_history import record_battlefield_acquisition
from .counter_placement import (
    commit_prepared_counter_placements,
    CounterPlacementError,
    PreparedCounterPlacements,
    prepare_counter_placement_specs,
)
from .entry_counters import (
    EntryCounterError,
    intrinsic_entry_counters,
    mark_intrinsic_entry_counters_initialized,
    validate_battle_entry_protector,
)
from .replacement_effects import (
    ReplacementChoiceRequired,
)
from .replacement.immutable import FrozenMap, thaw_value
from .semantic_runtime import (
    CounterPlacementEventSpec,
    TokenCreationReplacementContext,
    default_token_creation_replacement_registry,
    resolve_token_creation_replacements,
)
from .trigger_processing import enqueue_trigger_batch
from .standard_token_abilities import standard_token_characteristics
from .zone_trigger_events import sealed_public_characteristic_facts


class TokenCreationError(ValueError):
    pass


class TokenCreationHost(Protocol):
    """Narrow mutation port for authoritative token creation."""

    state: Any
    card_db: Any
    semantics: Any

    @property
    def seats(self) -> list[str]: ...

    @property
    def active_seats(self) -> list[str]: ...

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def _require_seat(self, seat: str, *, in_game: bool = False) -> Any: ...

    def _resolve_object(
        self, seat: str, ref: str, *, zones: set[str]
    ) -> Any: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _effective_card_data(
        self,
        card: Any,
        *,
        printed_entry_characteristics: bool = False,
    ) -> Mapping[str, Any]: ...

    def _compiled_enchant_spec(
        self,
        card: CardInstance,
        *,
        face_name: str | None = None,
    ) -> Any: ...

    def display_name(self, object_id: str) -> str: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def apnap_order(self, *, start: str | None = None) -> list[str]: ...

    def _next_zone_timestamp(self) -> int: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _stable_runtime_id(self, namespace: str, value: str) -> str: ...

    def _refresh_world_supertype_timestamp(
        self, card: CardInstance, *, gained_at: int
    ) -> None: ...

    def _attack_target_details(
        self, attacker: str, target: str
    ) -> Mapping[str, Any] | None: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        *,
        visibility: Iterable[str] | None = None,
        importance: int = 1,
        changed_objects: Iterable[str] | None = None,
        changed_players: Iterable[str] | None = None,
    ) -> Any: ...

    def _dispatch_semantic_event(
        self,
        event_kind: str,
        context: Mapping[str, Any],
        *,
        trigger_batch: list[Any],
    ) -> Any: ...


_ATTACKING_FIELD = "attacking"
_REASON_FIELD = "reason"


@dataclass(frozen=True, slots=True)
class PreparedTokenObject:
    """One prospective token identity pinned before counter replacement."""

    ref: str
    object_id: str
    oracle_id: str
    printed_name: str
    annotations: FrozenMap
    zone_timestamp: int
    tapped: bool
    attacking: str | None
    battle_protector: str | None
    temporary_keywords: tuple[str, ...]
    aura_target_ref: str | None
    replacement_component: FrozenMap | None


@dataclass(frozen=True, slots=True)
class ResolvedTokenSpecs:
    specs: tuple[Mapping[str, Any], ...]
    journal: tuple[Any, ...]
    remaining_selections: tuple[
        str | None | Mapping[str, Any], ...
    ]


def _creation_subject(
    host: TokenCreationHost,
    controller: str,
    *,
    name: str,
    quantity: int,
    copy_of: str | None,
    characteristics: Mapping[str, Any] | None,
) -> tuple[set[str], set[str], list[Any]]:
    if copy_of:
        copied_source = host._resolve_object(
            controller,
            str(copy_of),
            zones={"battlefield"},
        )
        created_types, created_subtypes, _ = host._type_parts(
            str(
                host._effective_card_data(copied_source).get("type_line")
                or ""
            )
        )
    else:
        type_line = str(
            dict(characteristics or {}).get("type_line") or ""
        )
        if not type_line:
            try:
                type_line = host.card_db.lookup(name).type_line
            except KeyError:
                type_line = ""
        created_types, created_subtypes, _ = host._type_parts(type_line)
    sources = (
        [
            host.state.cards[object_id]
            for object_id in list(
                host.state.players[controller].zones["battlefield"]
            )
            if host.state.cards[object_id].controller == controller
            and not host.state.cards[object_id].phased_out
        ]
        if quantity > 0
        else []
    )
    return created_types, created_subtypes, sources


def _token_replacement_effects(
    host: TokenCreationHost,
    controller: str,
    created_types: set[str],
    created_subtypes: set[str],
    sources: Sequence[Any],
) -> tuple[Any, ...]:
    registry = default_token_creation_replacement_registry()
    effects = []
    for source in sources:
        programs = host.semantics.runtime_handler_programs_for_oracle(
            source.oracle_id,
            active_zone="battlefield",
            event="token.create",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            for descriptor_index, descriptor in enumerate(program.handlers):
                runtime_descriptor = (
                    normalize_game_record_v3_runtime_handler(descriptor)
                    if host.semantics.runtime_handler_compatibility_enabled
                    else descriptor
                )
                effects.append(
                    registry.replacement_effect(
                        runtime_descriptor,
                        TokenCreationReplacementContext(
                            source_ref=source.ref,
                            source_controller=source.controller,
                            event_controller=controller,
                            created_types=tuple(sorted(created_types)),
                            created_subtypes=tuple(sorted(created_subtypes)),
                            component_id=f"{program.key}:{descriptor_index}",
                        ),
                    )
                )
    return tuple(effects)


def _resolved_token_specs(
    host: TokenCreationHost,
    controller: str,
    *,
    quantity: int,
    token_specs: tuple[Mapping[str, Any], ...],
    created_types: set[str],
    created_subtypes: set[str],
    replacement_effects: Sequence[Any],
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ],
) -> ResolvedTokenSpecs:
    if quantity > 0 and replacement_effects:
        resolution = resolve_token_creation_replacements(
            event_id=(
                f"token.create:{host.state.revision}:"
                f"{host.state.event_sequence + 1}"
            ),
            controller=controller,
            tokens=token_specs,
            created_types=tuple(sorted(created_types)),
            created_subtypes=tuple(sorted(created_subtypes)),
            effects=tuple(replacement_effects),
            apnap_order=host.apnap_order(),
            selections=tuple(replacement_selections),
            require_all_selections=False,
        )
        if resolution.pending is not None:
            raise ReplacementChoiceRequired(
                batch=resolution.batch,
                effects=tuple(replacement_effects),
                pending=resolution.pending,
            )
        return ResolvedTokenSpecs(
            specs=resolution.tokens,
            journal=resolution.journal,
            remaining_selections=tuple(
                replacement_selections[resolution.consumed_selections :]
            ),
        )
    return ResolvedTokenSpecs(
        specs=token_specs,
        journal=(),
        remaining_selections=tuple(replacement_selections),
    )


def _copied_token_identity(
    host: TokenCreationHost,
    controller: str,
    *,
    copy_of: Any,
    name: str,
    characteristics: Mapping[str, Any],
    ref: str,
) -> tuple[str, str, str, dict[str, Any]]:
    original = host._resolve_object(
        controller,
        str(copy_of),
        zones={"battlefield"},
    )
    annotations = copy.deepcopy(original.annotations)
    annotations["copied_from"] = original.object_id
    overrides = dict(annotations.get("copy_overrides") or {})
    if name:
        overrides["name"] = name
    overrides.update(characteristics)
    annotations["copy_overrides"] = overrides
    return (
        ref,
        original.oracle_id,
        name or host.display_name(original.object_id),
        annotations,
    )


def _new_token_identity(
    host: TokenCreationHost,
    *,
    name: str,
    characteristics: Mapping[str, Any],
    ref: str,
) -> tuple[str, str, str, dict[str, Any]]:
    try:
        record = host.card_db.lookup(name)
        oracle_id = record.oracle_id
        printed_name = record.name
    except KeyError:
        oracle_id = (
            "custom-token:"
            + host._stable_runtime_id("token-oracle", ref)
        )
        printed_name = name
    annotations = {
        "token_characteristics": copy.deepcopy(dict(characteristics))
    }
    if characteristics:
        annotations["copy_overrides"] = copy.deepcopy(
            dict(characteristics)
        )
    return ref, oracle_id, printed_name, annotations


def _preview_token_object(
    host: TokenCreationHost,
    controller: str,
    token_spec: Mapping[str, Any],
) -> CardInstance:
    """Build a nonauthoritative token object for entry preflight.

    No runtime IDs or refs are allocated here.  In particular, an Aura token
    prohibited by CR 303.4g must not consume object identity before the engine
    knows whether it can legally enter attached.
    """

    spec = dict(token_spec)
    characteristics = dict(spec.get("characteristics") or {})
    copy_of = spec.get("copy_of")
    raw_name = spec.get("name")
    name = str(raw_name) if raw_name is not None else ""
    if copy_of:
        original = host._resolve_object(
            controller,
            str(copy_of),
            zones={"battlefield"},
        )
        oracle_id = original.oracle_id
        printed_name = name or host.display_name(original.object_id)
        annotations = copy.deepcopy(original.annotations)
        overrides = dict(annotations.get("copy_overrides") or {})
        if name:
            overrides["name"] = name
        overrides.update(characteristics)
        annotations["copy_overrides"] = overrides
    else:
        if not name:
            name = "Token"
        try:
            record = host.card_db.lookup(name)
            oracle_id = record.oracle_id
            printed_name = record.name
        except KeyError:
            oracle_id = "custom-token:aura-entry-preview"
            printed_name = name
        annotations = {
            "token_characteristics": copy.deepcopy(characteristics),
        }
        if characteristics:
            annotations["copy_overrides"] = copy.deepcopy(
                characteristics
            )
    return CardInstance(
        object_id="aura-entry-preview",
        ref="aura-entry-preview",
        oracle_id=oracle_id,
        printed_name=printed_name,
        owner=controller,
        controller=controller,
        zone="outside",
        is_token=True,
        annotations=annotations,
        known_to=list(host.seats),
        revealed_to=list(host.seats),
    )


def _preflight_aura_token_specs(
    host: TokenCreationHost,
    controller: str,
    token_specs: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    prepared: list[Mapping[str, Any]] = []
    for raw_spec in token_specs:
        spec = dict(raw_spec)
        if int(spec.get("quantity", 1)) <= 0:
            prepared.append(spec)
            continue
        preview = _preview_token_object(host, controller, spec)
        data = host._effective_card_data(preview)
        card_types, subtypes, _supertypes = host._type_parts(
            str(data.get("type_line") or "")
        )
        try:
            spec["battle_protector"] = validate_battle_entry_protector(
                card_types=tuple(sorted(card_types)),
                subtypes=tuple(sorted(subtypes)),
                controller=controller,
                supplied_protector=(
                    str(spec["battle_protector"])
                    if spec.get("battle_protector") is not None
                    else None
                ),
                active_seats=host.active_seats,
            )
        except EntryCounterError as exc:
            raise TokenCreationError(str(exc)) from exc
        if not is_aura_type_line(str(data.get("type_line") or "")):
            prepared.append(spec)
            continue
        try:
            enchant_spec = host._compiled_enchant_spec(preview)
            if enchant_spec is None:
                raise AuraRuleError(
                    "Aura token entry requires one trusted compiled "
                    "Enchant descriptor"
                )
            plan = prepare_aura_entry(
                host,
                preview,
                spec=enchant_spec,
                controller=controller,
                target_ref=(
                    str(spec["aura_target_ref"])
                    if spec.get("aura_target_ref") is not None
                    else None
                ),
                resolving_as_spell=False,
            )
        except AuraEntryChoiceRequired as exc:
            raise TokenCreationError(
                "Aura token creation requires a legal attachment choice "
                "before any token is committed"
            ) from exc
        except AuraRuleError as exc:
            raise TokenCreationError(str(exc)) from exc
        if plan.outcome is AuraEntryOutcome.REMAIN_IN_ZONE:
            # Tokens are created directly on the battlefield.  If the Aura
            # cannot enter attached, CR 303.4g says it is not created.
            continue
        if plan.outcome is not AuraEntryOutcome.ENTER_ATTACHED:
            raise TokenCreationError(
                "Aura token entry produced an invalid preflight outcome"
            )
        spec["aura_target_ref"] = plan.target_ref
        prepared.append(spec)
    return tuple(prepared)


def _card_from_token_plan(
    host: TokenCreationHost,
    controller: str,
    plan: PreparedTokenObject,
) -> CardInstance:
    card = CardInstance(
        object_id=plan.object_id,
        ref=plan.ref,
        oracle_id=plan.oracle_id,
        printed_name=plan.printed_name,
        owner=controller,
        controller=controller,
        zone="battlefield",
        is_token=True,
        zone_timestamp=plan.zone_timestamp,
        tapped=plan.tapped,
        temporary_keywords=list(plan.temporary_keywords),
        annotations=thaw_value(plan.annotations),
        entered_battlefield_turn_sequence=host.state.turn_sequence,
        known_to=list(host.seats),
        revealed_to=list(host.seats),
        attacking=plan.attacking,
        battle_protector=plan.battle_protector,
    )
    record_battlefield_acquisition(host.state, card, plan.zone_timestamp)
    return card


def _commit_token_object(
    host: TokenCreationHost,
    controller: str,
    plan: PreparedTokenObject,
) -> str:
    allocated_ref = host._next_ref("T")
    if allocated_ref != plan.ref:
        raise TokenCreationError(
            "Prospective token identity changed before commit"
        )
    card = _card_from_token_plan(host, controller, plan)
    host.state.cards[card.object_id] = card
    host.state.players[controller].zones["battlefield"].append(card.object_id)
    host._refresh_world_supertype_timestamp(
        card,
        gained_at=card.zone_timestamp,
    )
    if plan.attacking:
        host.state.combat.attackers[card.object_id] = plan.attacking
        target_details = host._attack_target_details(
            controller, plan.attacking
        )
        if target_details is not None:
            host.state.combat.attack_target_context[
                card.object_id
            ] = target_details
    return card.object_id


def _prepare_token_objects(
    host: TokenCreationHost,
    controller: str,
    token_specs: Sequence[Mapping[str, Any]],
    *,
    creation_timestamp: int,
) -> tuple[PreparedTokenObject, ...]:
    plans: list[PreparedTokenObject] = []
    next_token_number = int(host.state.ref_counters.get("T", 0)) + 1
    for token_spec in token_specs:
        spec = dict(token_spec)
        spec_quantity = int(spec.get("quantity", 1))
        if spec_quantity < 0:
            raise TokenCreationError(
                "Replacement token quantity cannot be negative"
            )
        characteristics = dict(spec.get("characteristics") or {})
        copy_of = spec.get("copy_of")
        raw_name = spec.get("name")
        name = str(raw_name) if raw_name is not None else ""
        if not copy_of and not name:
            name = "Token"
        component = spec.get("replacement_component")
        frozen_component = (
            FrozenMap(component) if isinstance(component, Mapping) else None
        )
        for _ in range(spec_quantity):
            ref = f"T{next_token_number}"
            next_token_number += 1
            if copy_of:
                identity = _copied_token_identity(
                    host,
                    controller,
                    copy_of=copy_of,
                    name=name,
                    characteristics=characteristics,
                    ref=ref,
                )
            else:
                identity = _new_token_identity(
                    host,
                    name=name,
                    characteristics=characteristics,
                    ref=ref,
                )
            plans.append(
                PreparedTokenObject(
                    ref=identity[0],
                    object_id=host._stable_runtime_id(
                        "token-object", identity[0]
                    ),
                    oracle_id=identity[1],
                    printed_name=identity[2],
                    annotations=FrozenMap(identity[3]),
                    zone_timestamp=creation_timestamp,
                    tapped=bool(spec.get("tapped", False)),
                    attacking=(
                        str(spec[_ATTACKING_FIELD])
                        if spec.get(_ATTACKING_FIELD) is not None
                        else None
                    ),
                    battle_protector=(
                        str(spec["battle_protector"])
                        if spec.get("battle_protector") is not None
                        else None
                    ),
                    temporary_keywords=tuple(
                        str(value)
                        for value in spec.get("temporary_keywords", ())
                    ),
                    aura_target_ref=(
                        str(spec["aura_target_ref"])
                        if spec.get("aura_target_ref") is not None
                        else None
                    ),
                    replacement_component=frozen_component,
                )
            )
    return tuple(plans)


def _token_entry_counter_specs(
    host: TokenCreationHost,
    controller: str,
    plans: Sequence[PreparedTokenObject],
) -> tuple[CounterPlacementEventSpec, ...]:
    specs: list[CounterPlacementEventSpec] = []
    for plan in plans:
        card = _card_from_token_plan(host, controller, plan)
        try:
            data = host._effective_card_data(
                card,
                printed_entry_characteristics=True,
            )
            card_types, subtypes, supertypes = host._type_parts(
                str(data.get("type_line") or "")
            )
            counters = intrinsic_entry_counters(
                data,
                card_types=tuple(sorted(card_types)),
                card_subtypes=tuple(sorted(subtypes)),
                keywords=tuple(data.get("keywords") or ()),
            )
        except EntryCounterError as exc:
            raise TokenCreationError(str(exc)) from exc
        for index, counter in enumerate(counters):
            if counter.amount == 0:
                continue
            specs.append(
                CounterPlacementEventSpec(
                    event_id=(
                        f"token.entry-counter:{host.state.revision}:"
                        f"{host.state.event_sequence + 1}:{plan.ref}:"
                        f"{index}"
                    ),
                    subject_kind="permanent",
                    subject_id=plan.object_id,
                    owner=controller,
                    controller=controller,
                    target_zone="battlefield",
                    target_types=tuple(
                        sorted({*card_types, *subtypes, *supertypes})
                    ),
                    placing_player=controller,
                    counter_name=counter.counter_name,
                    amount=counter.amount,
                    source_ref=f"rule:{counter.rule_id}:{plan.ref}",
                    effect_generated=True,
                    logical_object_id=card.logical_object_id,
                    prospective_subject=True,
                )
            )
    return tuple(specs)


def _commit_token_specs(
    host: TokenCreationHost,
    controller: str,
    plans: Sequence[PreparedTokenObject],
    *,
    creation_timestamp: int,
    prepared_counters: PreparedCounterPlacements,
) -> tuple[list[str], list[dict[str, Any]]]:
    if plans:
        allocated_timestamp = host._next_zone_timestamp()
        if allocated_timestamp != creation_timestamp:
            raise TokenCreationError(
                "Prospective token timestamp changed before commit"
            )
    created: list[str] = []
    applied_components: list[dict[str, Any]] = []
    for plan in plans:
        object_id = _commit_token_object(host, controller, plan)
        created.append(object_id)
        if plan.replacement_component is not None:
            component = thaw_value(plan.replacement_component)
            if component not in applied_components:
                applied_components.append(component)
        if plan.aura_target_ref is not None:
            token = host.state.cards[object_id]
            data = host._effective_card_data(token)
            try:
                enchant_spec = host._compiled_enchant_spec(token)
                if enchant_spec is None:
                    raise AuraRuleError(
                        "Aura token entry requires one trusted compiled "
                        "Enchant descriptor"
                    )
                aura_plan = prepare_aura_entry(
                    host,
                    token,
                    spec=enchant_spec,
                    controller=controller,
                    target_ref=plan.aura_target_ref,
                    resolving_as_spell=False,
                )
                commit_aura_entry_attachment(host, token, aura_plan)
            except AuraRuleError as exc:
                raise TokenCreationError(str(exc)) from exc
    try:
        if prepared_counters.events:
            commit_prepared_counter_placements(
                host,
                prepared_counters,
                reason="intrinsic token entry counters",
            )
        for object_id in created:
            token = host.state.cards[object_id]
            data = host._effective_card_data(
                token,
                printed_entry_characteristics=True,
            )
            mark_intrinsic_entry_counters_initialized(
                token,
                destination="battlefield",
                destination_type_line=str(data.get("type_line") or ""),
            )
    except (CounterPlacementError, EntryCounterError) as exc:
        raise TokenCreationError(str(exc)) from exc
    return created, applied_components


def _record_and_dispatch_token_creation(
    host: TokenCreationHost,
    controller: str,
    created: Sequence[str],
    *,
    name: str,
    base_quantity: int,
    replacement_components: Sequence[Mapping[str, Any]],
    replacement_journal: Sequence[Any],
    reason: str,
) -> None:
    tracker = host.state.players[controller].stats.setdefault(
        "tokens_created_by_turn", {}
    )
    turn_key = str(host.state.turn_sequence)
    tracker[turn_key] = int(tracker.get(turn_key, 0)) + len(created)
    host._log(
        controller,
        "token.create",
        f"{controller} created {len(created)} token(s).",
        {
            "objects": [
                host.state.cards[object_id].ref for object_id in created
            ],
            "base_name": name,
            "base_quantity": base_quantity,
            "replacement_count": len(created) - base_quantity,
            "replacement_components": [
                dict(value) for value in replacement_components
            ],
            "replacement_order": [
                selection.effect_id for selection in replacement_journal
            ],
            _REASON_FIELD: reason,
        },
        importance=1,
        changed_objects=created,
        changed_players=[controller],
    )
    trigger_batch: list[Any] = []
    for object_id in created:
        card = host.state.cards[object_id]
        data = host._effective_card_data(card)
        characteristic_facts = sealed_public_characteristic_facts(data)
        types = set(characteristic_facts["types"])
        context = {
            "card": card.ref,
            "controller": controller,
            "owner": controller,
            "from": "outside",
            "to": "battlefield",
            **characteristic_facts,
            "mana_value": float(data.get("mana_value", 0) or 0),
            "token": True,
            "tapped": card.tapped,
            _REASON_FIELD: reason,
        }
        host._dispatch_semantic_event(
            "token.created", context, trigger_batch=trigger_batch
        )
        host._dispatch_semantic_event(
            "permanent.enter", context, trigger_batch=trigger_batch
        )
        for card_type in ("artifact", "creature", "land", "enchantment"):
            if card_type in types:
                host._dispatch_semantic_event(
                    f"{card_type}.enter",
                    context,
                    trigger_batch=trigger_batch,
                )
    enqueue_trigger_batch(host, trigger_batch)


def _create_token_specs(
    host: TokenCreationHost,
    controller: str,
    *,
    token_specs: Sequence[Mapping[str, Any]],
    created_types: set[str],
    created_subtypes: set[str],
    replacement_sources: Sequence[Any],
    base_name: str,
    base_quantity: int,
    reason: str,
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ],
) -> list[str]:
    replacement_effects = _token_replacement_effects(
        host,
        controller,
        created_types,
        created_subtypes,
        replacement_sources,
    )
    resolved = _resolved_token_specs(
        host,
        controller,
        quantity=base_quantity,
        token_specs=token_specs,
        created_types=created_types,
        created_subtypes=created_subtypes,
        replacement_effects=replacement_effects,
        replacement_selections=replacement_selections,
    )
    resolved_specs = _preflight_aura_token_specs(
        host,
        controller,
        resolved.specs,
    )
    creation_timestamp = (
        host.state.timestamp_sequence + 1 if resolved_specs else 0
    )
    plans = _prepare_token_objects(
        host,
        controller,
        resolved_specs,
        creation_timestamp=creation_timestamp,
    )
    try:
        prepared_counters = prepare_counter_placement_specs(
            host,
            _token_entry_counter_specs(host, controller, plans),
            selections=resolved.remaining_selections,
            batch_id=(
                f"replacement:token.entry-counter:{host.state.revision}:"
                f"{host.state.event_sequence + 1}"
            ),
        )
    except CounterPlacementError as exc:
        raise TokenCreationError(str(exc)) from exc
    created, applied_components = _commit_token_specs(
        host,
        controller,
        plans,
        creation_timestamp=creation_timestamp,
        prepared_counters=prepared_counters,
    )
    _record_and_dispatch_token_creation(
        host,
        controller,
        created,
        name=base_name,
        base_quantity=base_quantity,
        replacement_components=applied_components,
        replacement_journal=resolved.journal,
        reason=reason,
    )
    return [host.state.cards[object_id].ref for object_id in created]


def create_tokens(
    host: TokenCreationHost,
    controller: str,
    *,
    name: str,
    quantity: int = 1,
    tapped: bool = False,
    attacking: str | None = None,
    battle_protector: str | None = None,
    copy_of: str | None = None,
    characteristics: Mapping[str, Any] | None = None,
    temporary_keywords: Sequence[str] = (),
    aura_target_ref: str | None = None,
    reason: str = "token effect",
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ] = (),
) -> list[str]:
    """Resolve creation replacements, commit tokens, and emit enter events."""

    host._require_seat(controller, in_game=True)
    if quantity < 0:
        raise TokenCreationError("Token quantity cannot be negative")
    if not copy_of:
        try:
            characteristics = standard_token_characteristics(characteristics)
        except ValueError as exc:
            raise TokenCreationError(str(exc)) from exc
    created_types, created_subtypes, sources = _creation_subject(
        host,
        controller,
        name=name,
        quantity=quantity,
        copy_of=copy_of,
        characteristics=characteristics,
    )
    return _create_token_specs(
        host,
        controller,
        token_specs=(
            {
                "name": name,
                "quantity": quantity,
                "tapped": tapped,
                _ATTACKING_FIELD: attacking,
                "battle_protector": battle_protector,
                "copy_of": copy_of,
                "characteristics": copy.deepcopy(
                    dict(characteristics or {})
                ),
                "temporary_keywords": list(temporary_keywords),
                "aura_target_ref": aura_target_ref,
            },
        ),
        created_types=created_types,
        created_subtypes=created_subtypes,
        replacement_sources=sources,
        base_name=name,
        base_quantity=quantity,
        reason=reason,
        replacement_selections=replacement_selections,
    )


def create_token_batch(
    host: TokenCreationHost,
    controller: str,
    *,
    tokens: Sequence[Mapping[str, Any]],
    reason: str = "token effect",
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ] = (),
) -> list[str]:
    """Create two or three fixed token definitions as one event."""

    host._require_seat(controller, in_game=True)
    if not isinstance(tokens, (list, tuple)) or not 2 <= len(tokens) <= 3:
        raise TokenCreationError(
            "Fixed token batches require two or three definitions"
        )
    normalized_specs: list[dict[str, Any]] = []
    created_types: set[str] = set()
    created_subtypes: set[str] = set()
    replacement_sources: dict[str, Any] = {}
    base_quantity = 0
    for index, supplied in enumerate(tokens):
        if not isinstance(supplied, Mapping):
            raise TokenCreationError(
                f"Token batch definition {index} must be an object"
            )
        allowed = {"name", "quantity", "characteristics", "tapped"}
        unknown = sorted(set(supplied) - allowed)
        if unknown:
            raise TokenCreationError(
                "Token batch definition has unknown fields: "
                + ", ".join(unknown)
            )
        name = supplied.get("name")
        quantity = supplied.get("quantity")
        characteristics = supplied.get("characteristics")
        tapped = supplied.get("tapped", False)
        if type(name) is not str or not name.strip():
            raise TokenCreationError(
                "Token batch definition requires a nonempty name"
            )
        if type(quantity) is not int or quantity < 1:
            raise TokenCreationError(
                "Token batch definition quantity must be positive"
            )
        if not isinstance(characteristics, Mapping):
            raise TokenCreationError(
                "Token batch definition requires characteristics"
            )
        if type(tapped) is not bool:
            raise TokenCreationError(
                "Token batch tapped state must be boolean"
            )
        try:
            normalized_characteristics = standard_token_characteristics(
                characteristics
            )
        except ValueError as exc:
            raise TokenCreationError(str(exc)) from exc
        types, subtypes, sources = _creation_subject(
            host,
            controller,
            name=name.strip(),
            quantity=quantity,
            copy_of=None,
            characteristics=normalized_characteristics,
        )
        created_types.update(types)
        created_subtypes.update(subtypes)
        replacement_sources.update(
            {source.object_id: source for source in sources}
        )
        base_quantity += quantity
        normalized_specs.append(
            {
                "name": name.strip(),
                "quantity": quantity,
                "tapped": tapped,
                _ATTACKING_FIELD: None,
                "battle_protector": None,
                "copy_of": None,
                "characteristics": copy.deepcopy(
                    normalized_characteristics
                ),
                "temporary_keywords": [],
                "aura_target_ref": None,
            }
        )
    return _create_token_specs(
        host,
        controller,
        token_specs=tuple(normalized_specs),
        created_types=created_types,
        created_subtypes=created_subtypes,
        replacement_sources=tuple(replacement_sources.values()),
        base_name="fixed token batch",
        base_quantity=base_quantity,
        reason=reason,
        replacement_selections=replacement_selections,
    )
