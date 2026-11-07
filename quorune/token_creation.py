from __future__ import annotations

from .created_token_cleanup import CreatedTokenCleanupPlan, capture_created_token_cleanup, schedule_created_token_cleanup
from .entry_copy import copiable_oracle_id
from .token_creation_events import (
    CommittedTokenCreation, DistributedTokenChoiceRequired, public_replacement_labels, publish_token_creations,
)

import copy
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Protocol, Sequence, TYPE_CHECKING

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
from .token_creation_identity import (
    TokenCreationError, token_copy_snapshot, _copied_token_identity,
    _new_token_identity, _preview_token_object,
)
from .control_history import record_battlefield_acquisition
from .counter_placement import (
    commit_prepared_counter_placements,
    CounterPlacementError,
    PreparedCounterPlacements,
)
from .entry_counters import (
    EntryCounterError,
    mark_intrinsic_entry_counters_initialized,
    validate_battle_entry_protector,
)
from .entry_counter_model import EffectEntryCounter
from .replacement_effects import (
    ReplacementChoiceRequired,
)
from .replacement.immutable import FrozenMap, thaw_value
from .semantic_runtime import (
    TokenCreationReplacementContext,
    default_token_creation_replacement_registry,
    resolve_token_creation_replacements,
)
from .standard_token_abilities import standard_token_characteristics
from .defined_token_faces import DEFINED_TOKEN_ACTIVE_FACE_FIELD
from .semantic_runtime.zone_replacements import (
    capture_zone_change_replacement_snapshot,
    prepare_zone_change_replacement_snapshot,
    log_applied_zone_replacements,
)
from .semantic_runtime.zone_replacement_model import PreparedZoneChange


if TYPE_CHECKING:
    from .copied_token_aura_entry import CopiedAuraTokenSelection


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

    def _copyable_characteristics(self, card: Any) -> Mapping[str, Any]: ...

    def card_record(self, card: Any) -> Any: ...

    def _effective_static_component_keys(
        self,
        card: Any,
        *,
        prospective_zone: str | None = None,
        prospective_controller: str | None = None,
    ) -> tuple[str, ...]: ...

    def _compiled_enchant_spec(
        self,
        card: CardInstance,
        *,
        face_name: str | None = None,
    ) -> Any: ...

    def display_name(self, object_id: str) -> str: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _effective_static_component_key_map(self) -> Mapping[str,Sequence[str]]: ...

    def apnap_order(self, *, start: str | None = None) -> list[str]: ...

    def _next_zone_timestamp(self) -> int: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _stable_runtime_id(self, namespace: str, value: str) -> str: ...

    def _record_turn_history(self, kind: str, **kwargs: Any) -> None: ...

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
_ATTACKING_GROUP_FIELD = "_attacking_group"
_REASON_FIELD = "reason"


@dataclass(frozen=True, slots=True)
class GroupedTokenCreation:
    refs: tuple[str, ...]
    grouped_refs: tuple[tuple[str, str], ...]


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
    effect_entry_counters: tuple[EffectEntryCounter, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedTokenSpecs:
    specs: tuple[Mapping[str, Any], ...]
    journal: tuple[Any, ...]
    remaining_selections: tuple[
        str | None | Mapping[str, Any], ...
    ]


def _token_specification_inputs(
    host: TokenCreationHost,
    controller: str,
    *,
    name: str,
    quantity: int,
    tapped: bool,
    attacking: str | Sequence[str] | None,
    attacking_groups: Sequence[str],
    battle_protector: str | None,
    copy_of: str | None,
    copy_source_zone: str,
    copy_snapshot: Mapping[str, Any] | None,
    characteristics: Mapping[str, Any] | None,
    temporary_keywords: Sequence[str],
    aura_target_ref: str | None,
    effect_entry_counters: Sequence[EffectEntryCounter] = (),
) -> tuple[
    tuple[Mapping[str, Any], ...],
    set[str],
    set[str],
    list[Any],
]:
    normalized_characteristics = characteristics
    if not copy_of:
        try:
            normalized_characteristics = standard_token_characteristics(
                characteristics
            )
        except ValueError as exc:
            raise TokenCreationError(str(exc)) from exc
    created_types, created_subtypes, sources = _creation_subject(
        host,
        controller,
        name=name,
        quantity=quantity,
        copy_of=copy_of,
        characteristics=normalized_characteristics,
        copy_source_zone=copy_source_zone,
        copy_snapshot=copy_snapshot,
    )
    base = {
        "name": name,
        "quantity": 1,
        "tapped": tapped,
        "battle_protector": battle_protector,
        "copy_of": copy_of,
        "copy_source_zone": copy_source_zone,
        **(
            {"copy_snapshot": copy.deepcopy(dict(copy_snapshot))}
            if copy_snapshot is not None
            else {}
        ),
        "characteristics": copy.deepcopy(
            dict(normalized_characteristics or {})
        ),
        "temporary_keywords": list(temporary_keywords),
        "aura_target_ref": aura_target_ref,
        **({'effect_entry_counters':[counter.to_dict() for counter in effect_entry_counters]}
           if effect_entry_counters else {}),
    }
    groups = tuple(attacking_groups)
    if groups:
        if (
            len(groups) != quantity
            or any(type(value) is not str or not value for value in groups)
            or attacking is not None
        ):
            raise TokenCreationError(
                "Attacking token associations must match the base quantity"
            )
        token_specs = tuple(
            {**base, _ATTACKING_GROUP_FIELD: group}
            for group in groups
        )
    elif isinstance(attacking, (list, tuple)):
        assignments = tuple(attacking)
        if len(assignments) != quantity or any(
            type(value) is not str or not value for value in assignments
        ):
            raise TokenCreationError(
                "Attacking token assignments must match the token quantity"
            )
        token_specs = tuple(
            {**base, _ATTACKING_FIELD: destination}
            for destination in assignments
        )
    else:
        token_specs = (
            {
                **base,
                "quantity": quantity,
                _ATTACKING_FIELD: attacking,
            },
        )
    return token_specs, created_types, created_subtypes, sources


def preview_token_creation(
    host: TokenCreationHost,
    controller: str,
    *,
    name: str,
    quantity: int,
    tapped: bool = False,
    attacking_groups: Sequence[str],
    copy_of: str | None = None,
    copy_source_zone: str = "battlefield",
    copy_snapshot: Mapping[str, Any] | None = None,
    characteristics: Mapping[str, Any] | None = None,
    temporary_keywords: Sequence[str] = (),
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ] = (),
) -> ResolvedTokenSpecs:
    """Resolve token replacements without allocating or committing objects."""

    host._require_seat(controller, in_game=True)
    if quantity < 0:
        raise TokenCreationError("Token quantity cannot be negative")
    token_specs, created_types, created_subtypes, sources = (
        _token_specification_inputs(
            host,
            controller,
            name=name,
            quantity=quantity,
            tapped=tapped,
            attacking=None,
            attacking_groups=attacking_groups,
            battle_protector=None,
            copy_of=copy_of,
            copy_source_zone=copy_source_zone,
            copy_snapshot=copy_snapshot,
            characteristics=characteristics,
            temporary_keywords=temporary_keywords,
            aura_target_ref=None,
        )
    )
    effects = _token_replacement_effects(
        host,
        controller,
        created_types,
        created_subtypes,
        sources,
    )
    return _resolved_token_specs(
        host,
        controller,
        quantity=quantity,
        token_specs=token_specs,
        created_types=created_types,
        created_subtypes=created_subtypes,
        replacement_effects=effects,
        replacement_selections=replacement_selections,
    )


def _assign_expanded_attacking_destinations(
    token_specs: Sequence[Mapping[str, Any]],
    assignments: Sequence[str],
) -> tuple[Mapping[str, Any], ...]:
    supplied = tuple(assignments)
    if any(type(value) is not str or not value for value in supplied):
        raise TokenCreationError(
            "Expanded attacking assignments must be nonempty strings"
        )
    assigned: list[Mapping[str, Any]] = []
    index = 0
    for raw_spec in token_specs:
        spec = dict(raw_spec)
        group = spec.pop(_ATTACKING_GROUP_FIELD, None)
        quantity = int(spec.get("quantity", 1))
        if group is None:
            assigned.append(spec)
            continue
        for _ in range(quantity):
            if index >= len(supplied):
                raise TokenCreationError(
                    "Expanded attacking assignments are incomplete"
                )
            assigned.append(
                {
                    **spec,
                    "quantity": 1,
                    _ATTACKING_FIELD: supplied[index],
                }
            )
            index += 1
    if index != len(supplied):
        raise TokenCreationError(
            "Expanded attacking assignments exceed the token plan"
        )
    return tuple(assigned)


def _detach_creation_groups(
    token_specs: Sequence[Mapping[str, Any]],
) -> tuple[tuple[Mapping[str, Any], ...], tuple[str | None, ...]]:
    cleaned: list[Mapping[str, Any]] = []
    groups: list[str | None] = []
    for raw_spec in token_specs:
        spec = dict(raw_spec)
        group = spec.pop(_ATTACKING_GROUP_FIELD, None)
        quantity = int(spec.get("quantity", 1))
        if quantity < 0:
            raise TokenCreationError(
                "Replacement token quantity cannot be negative"
            )
        if group is not None and (type(group) is not str or not group):
            raise TokenCreationError(
                "Token creation groups must be nonempty strings"
            )
        cleaned.append(spec)
        groups.extend([group] * quantity)
    return tuple(cleaned), tuple(groups)


def _creation_subject(
    host: TokenCreationHost,
    controller: str,
    *,
    name: str,
    quantity: int,
    copy_of: str | None,
    characteristics: Mapping[str, Any] | None,
    copy_source_zone: str = "battlefield",
    copy_snapshot: Mapping[str, Any] | None = None,
) -> tuple[set[str], set[str], list[Any]]:
    if copy_of:
        copied_source = (
            None
            if copy_snapshot is not None
            else host._resolve_object(
                controller,
                str(copy_of),
                zones={copy_source_zone},
            )
        )
        copied_data = (
            dict(copy_snapshot.get("characteristics") or {})
            if copy_snapshot is not None
            else host._copyable_characteristics(copied_source)
        )
        created_types, created_subtypes, _ = host._type_parts(
            str(copied_data.get("type_line") or "")
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
    component_map = None
    for source in sources:
        programs = host.semantics.runtime_handler_programs_for_oracle(
            copiable_oracle_id(source),
            active_zone="battlefield",
            event="token.create",
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
            if CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage:
                if component_map is None:
                    component_map=host._effective_static_component_key_map()
                if program.key not in component_map.get(source.object_id,()):
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


def _preflight_aura_token_specs(
    host: TokenCreationHost,
    controller: str,
    token_specs: Sequence[Mapping[str, Any]],
    *,
    copied_aura_selections: Sequence[CopiedAuraTokenSelection] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    prepared: list[Mapping[str, Any]] = []
    selected = {choice.slot: choice for choice in copied_aura_selections or ()}
    for spec_index, raw_spec in enumerate(token_specs):
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
            if copied_aura_selections is None:
                raise TokenCreationError(
                    "Aura token creation requires a legal attachment choice "
                    "before any token is committed"
                ) from exc
            from .copied_token_aura_entry import prepare_copied_aura_token_specs

            prepared.extend(prepare_copied_aura_token_specs(host, preview, spec, exc, spec_index, selected))
            continue
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
    if selected:
        raise TokenCreationError("Copied Aura token slots changed before entry")
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
        active_face=(plan.annotations.get('copy_overrides') or {}).get(DEFINED_TOKEN_ACTIVE_FACE_FIELD),
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
    from .game_object_store import GameObjectStore
    GameObjectStore(host).commit(upserts=(card,))
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
    reference_offset: int = 0,
) -> tuple[PreparedTokenObject, ...]:
    plans: list[PreparedTokenObject] = []
    next_token_number = int(host.state.ref_counters.get("T", 0)) + 1 + reference_offset
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
                    copy_source_zone=str(
                        spec.get("copy_source_zone") or "battlefield"
                    ),
                    copy_snapshot=(
                        spec.get("copy_snapshot")
                        if isinstance(spec.get("copy_snapshot"), Mapping)
                        else None
                    ),
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
                    effect_entry_counters=tuple(EffectEntryCounter.from_dict(value)
                        for value in spec.get('effect_entry_counters', ())),
                )
            )
    return tuple(plans)


def _prepare_token_entry_counters(
    host: TokenCreationHost,
    controller: str,
    plans: Sequence[PreparedTokenObject],
    selections: Sequence[str | None | Mapping[str, Any]],
    *,
    controllers: Mapping[str, str] | None = None,
) -> tuple[
    tuple[PreparedTokenObject, ...],
    PreparedCounterPlacements,
    Mapping[str, PreparedZoneChange],
]:
    """Prepare the canonical entry tree without allocating live objects."""
    if not plans:
        return (), PreparedCounterPlacements((), (), ()), {}
    objects: dict[str, CardInstance] = {}
    characteristics: dict[str, Mapping[str, Any]] = {}
    entry_controllers = controllers if controllers is not None else {plan.object_id: controller for plan in plans}
    for plan in plans:
        card = _card_from_token_plan(host, entry_controllers[plan.object_id], plan)
        characteristics[card.object_id] = host._effective_card_data(
            card, printed_entry_characteristics=True,
        )
        card.zone = "outside"
        objects[card.object_id] = card
    snapshot = capture_zone_change_replacement_snapshot(
        host,
        tuple((key, "battlefield") for key in objects),
        destination_controllers=entry_controllers,
        entry_characteristics=characteristics,
        prospective_objects=objects,
        requested_tapped={plan.object_id: plan.tapped for plan in plans},
        effect_entry_counters={plan.object_id:plan.effect_entry_counters for plan in plans},
        error_type=TokenCreationError,
    )
    prepared = prepare_zone_change_replacement_snapshot(
        snapshot, selections=selections, error_type=TokenCreationError,
    )
    if any(
        entry.destination != "battlefield"
        or entry.entry_life_payment
        or entry.keyword_grants
        or entry.read_ahead_chapter is not None
        for entry in prepared.values()
    ):
        raise TokenCreationError(
            "Token entry replacement requires an unrepresented noncounter result"
        )
    events = tuple(event for entry in prepared.values() for event in entry.counter_events)
    counter_ids = {event.event_id for event in events}
    journal = tuple(
        selection for entry in prepared.values() for selection in entry.journal
        if selection.event_id in counter_ids
    )
    entry_plans = tuple(
        replace(plan, tapped=prepared[plan.object_id].entry_tapped) for plan in plans
    )
    return entry_plans, PreparedCounterPlacements(events, snapshot.effects, journal), prepared


def _committed_replacement_components(plans: Sequence[PreparedTokenObject]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for plan in plans:
        if plan.replacement_component is not None:
            component = thaw_value(plan.replacement_component)
            if component not in components:
                components.append(component)
    return components


def _commit_token_specs(
    host: TokenCreationHost,
    controller: str,
    plans: Sequence[PreparedTokenObject],
    *,
    creation_timestamp: int,
    prepared_counters: PreparedCounterPlacements,
    prepared_entries: Mapping[str, PreparedZoneChange] | None = None,
    ordered_role_timestamps: bool = False,
    controllers: Mapping[str, str] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    if plans:
        allocated_timestamp = host._next_zone_timestamp()
        if allocated_timestamp != creation_timestamp:
            raise TokenCreationError(
                "Prospective token timestamp changed before commit"
            )
        if ordered_role_timestamps:
            for expected in range(creation_timestamp + 1, creation_timestamp + len(plans)):
                if host._next_zone_timestamp() != expected:
                    raise TokenCreationError('Ordered Role timestamps changed before commit')
    created: list[str] = []
    for plan in plans:
        token_controller = controllers[plan.object_id] if controllers is not None else controller
        object_id = _commit_token_object(host, token_controller, plan)
        created.append(object_id)
        if prepared_entries is not None:
            from .entry_results import commit_entry_designations
            entry = prepared_entries[object_id]
            from .entry_results import commit_prepared_copy_values
            commit_prepared_copy_values(entry,host.state.cards[object_id],error_type=TokenCreationError)
            commit_entry_designations(host, host.state.cards[object_id], color=entry.entry_chosen_color, creature_type=entry.entry_chosen_creature_type)
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
                    controller=token_controller,
                    target_ref=plan.aura_target_ref,
                    resolving_as_spell=False,
                )
                commit_aura_entry_attachment(host, token, aura_plan,
                    source_timestamp=plan.zone_timestamp if ordered_role_timestamps else None)
            except AuraRuleError as exc:
                raise TokenCreationError(str(exc)) from exc
    try:
        if prepared_counters.events:
            commit_prepared_counter_placements(
                host,
                prepared_counters,
                reason="intrinsic token entry counters",
                dispatch_events=False,
            )
        for object_id in created:
            token = host.state.cards[object_id]
            if prepared_entries is not None:
                log_applied_zone_replacements(
                    host, prepared_entries[object_id], token,
                    requested_destination="battlefield", error_type=TokenCreationError,
                )
                from .preparation_model import PREPARE_ENTRY_FIELD,PrepareSpellSpec
                if PREPARE_ENTRY_FIELD in prepared_entries[object_id].event.payload:
                    from .preparation import become_prepared
                    become_prepared(host,token,spell=PrepareSpellSpec.from_dict(prepared_entries[object_id].event.payload[PREPARE_ENTRY_FIELD]))
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
    return created, _committed_replacement_components(plans)


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
    return _commit_resolved_token_specs(
        host,
        controller,
        resolved=resolved,
        base_name=base_name,
        base_quantity=base_quantity,
        reason=reason,
    )


def _commit_prepared_token_entries(
    host: TokenCreationHost,
    controller: str,
    plans: Sequence[PreparedTokenObject],
    *,
    creation_timestamp: int,
    prepared_counters: PreparedCounterPlacements,
    prepared_entries: Mapping[str, PreparedZoneChange],
    reason: str,
    ordered_role_timestamps: bool = False,
    controllers: Mapping[str, str] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    from .zone_devour import (
        commit_token_devour_sacrifices, prepare_token_devour_sacrifices, publish_token_devour_sacrifices,
    )

    devour = prepare_token_devour_sacrifices(host, prepared_entries, reason=reason)
    sacrifices = commit_token_devour_sacrifices(devour, timestamp=creation_timestamp, reason=reason)
    result = _commit_token_specs(
        host, controller, plans,
        creation_timestamp=creation_timestamp,
        prepared_counters=prepared_counters,
        prepared_entries=prepared_entries,
        ordered_role_timestamps=ordered_role_timestamps,
        controllers=controllers,
    )
    publish_token_devour_sacrifices(sacrifices, reason=reason)
    return result


def _commit_resolved_token_specs(
    host: TokenCreationHost,
    controller: str,
    *,
    resolved: ResolvedTokenSpecs,
    base_name: str,
    base_quantity: int,
    reason: str,
    role_timestamp_order: Sequence[int] | None = None,
    token_cleanup: CreatedTokenCleanupPlan | None = None,
    copied_aura_selections: Sequence[CopiedAuraTokenSelection] | None = None,
) -> list[str]:
    resolved_specs = _preflight_aura_token_specs(
        host, controller, resolved.specs, copied_aura_selections=copied_aura_selections,
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
    if role_timestamp_order is not None:
        from .role_token_creation import ordered_role_plans
        plans = ordered_role_plans(plans, role_timestamp_order, creation_timestamp)
    try:
        plans, prepared_counters, prepared_entries = _prepare_token_entry_counters(
            host, controller, plans, resolved.remaining_selections,
        )
    except CounterPlacementError as exc:
        raise TokenCreationError(str(exc)) from exc
    created, applied_components = _commit_prepared_token_entries(
        host, controller, plans,
        creation_timestamp=creation_timestamp,
        prepared_counters=prepared_counters,
        prepared_entries=prepared_entries,
        ordered_role_timestamps=role_timestamp_order is not None,
        reason=reason,
    )
    from .token_copy_runtime import finish_copy_aftercare
    publish_token_creations(
        host,
        (CommittedTokenCreation(
            controller, tuple(created), base_name, base_quantity, tuple(applied_components), resolved.journal,
        ),),
        reason=reason,
        prepared_counters=prepared_counters,
    )
    # A separate subsequent grant is not an entry modification. Discovery
    # remains deferred; this does not introduce priority or an SBA checkpoint.
    finish_copy_aftercare(host, controller, created)
    if token_cleanup is not None:
        schedule_created_token_cleanup(host, controller, (host.state.cards[oid] for oid in created), token_cleanup.spec,
            label='Token cleanup', source=token_cleanup.source, source_binding=token_cleanup.source_binding)
    return [host.state.cards[object_id].ref for object_id in created]


def create_tokens(
    host: TokenCreationHost,
    controller: str,
    *,
    name: str,
    quantity: int = 1,
    tapped: bool = False,
    attacking: str | Sequence[str] | None = None,
    attacking_groups: Sequence[str] = (),
    battle_protector: str | None = None,
    copy_of: str | None = None,
    copy_source_zone: str = "battlefield",
    copy_snapshot: Mapping[str, Any] | None = None,
    characteristics: Mapping[str, Any] | None = None,
    temporary_keywords: Sequence[str] = (),
    aura_target_ref: str | None = None,
    reason: str = "token effect",
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ] = (),
    effect_entry_counters: Sequence[EffectEntryCounter] = (),
    token_cleanup: CreatedTokenCleanupPlan | None = None,
    copied_aura_selections: Sequence[CopiedAuraTokenSelection] | None = None,
) -> list[str]:
    """Resolve creation replacements, commit tokens, and emit enter events."""

    if not isinstance(effect_entry_counters,(list,tuple)) or any(
            not isinstance(value,EffectEntryCounter) for value in effect_entry_counters):
        raise TokenCreationError('Token entry counters require typed effect instructions')
    host._require_seat(controller, in_game=True)
    if token_cleanup is not None:
        try:token_cleanup = capture_created_token_cleanup(host, token_cleanup, controller=controller)
        except (ValueError,TypeError) as exc:raise TokenCreationError(str(exc)) from exc
    if copy_source_zone not in {"battlefield", "exile", "graveyard"}:
        raise TokenCreationError("Token-copy source zone is unsupported")
    if copy_source_zone == "graveyard" and copy_snapshot is None:
        raise TokenCreationError("Graveyard token-copy source requires a pinned snapshot")
    if copy_source_zone != "battlefield" and not copy_of:
        raise TokenCreationError(
            "A nonbattlefield token-copy zone requires a copy source"
        )
    if copy_snapshot is not None and not isinstance(copy_snapshot, Mapping):
        raise TokenCreationError("Token copy snapshot must be an object")
    if quantity < 0:
        raise TokenCreationError("Token quantity cannot be negative")
    expanded_assignments = (
        tuple(attacking)
        if attacking_groups and isinstance(attacking, (list, tuple))
        else ()
    )
    token_specs, created_types, created_subtypes, sources = (
        _token_specification_inputs(
            host,
            controller,
            name=name,
            quantity=quantity,
            tapped=tapped,
            attacking=(None if attacking_groups else attacking),
            attacking_groups=attacking_groups,
            battle_protector=battle_protector,
            copy_of=copy_of,
            copy_source_zone=copy_source_zone,
            copy_snapshot=copy_snapshot,
            characteristics=characteristics,
            temporary_keywords=temporary_keywords,
            aura_target_ref=aura_target_ref,
            effect_entry_counters=effect_entry_counters,
        )
    )
    replacement_effects = _token_replacement_effects(
        host,
        controller,
        created_types,
        created_subtypes,
        sources,
    )
    resolved = _resolved_token_specs(
        host,
        controller,
        quantity=quantity,
        token_specs=token_specs,
        created_types=created_types,
        created_subtypes=created_subtypes,
        replacement_effects=replacement_effects,
        replacement_selections=replacement_selections,
    )
    resolved_specs = (
        _assign_expanded_attacking_destinations(
            resolved.specs,
            expanded_assignments,
        )
        if attacking_groups
        else resolved.specs
    )
    return _commit_resolved_token_specs(
        host,
        controller,
        resolved=ResolvedTokenSpecs(
            specs=resolved_specs,
            journal=resolved.journal,
            remaining_selections=resolved.remaining_selections,
        ),
        base_name=name,
        base_quantity=quantity,
        reason=reason,
        token_cleanup=token_cleanup,
        copied_aura_selections=copied_aura_selections,
    )


def create_grouped_tokens(
    host: TokenCreationHost,
    controller: str,
    *,
    groups: Sequence[str],
    name: str = "",
    copy_of: str | None = None,
    copy_source_zone: str = "battlefield",
    copy_snapshot: Mapping[str, Any] | None = None,
    characteristics: Mapping[str, Any] | None = None,
    temporary_keywords: Sequence[str] = (),
    reason: str = "grouped token effect",
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ] = (),
) -> GroupedTokenCreation:
    """Create one replacement-aware token event while retaining group identity."""

    normalized_groups = tuple(groups)
    if not normalized_groups or any(
        type(value) is not str or not value for value in normalized_groups
    ):
        raise TokenCreationError(
            "Grouped token creation requires nonempty string groups"
        )
    if len(normalized_groups) != len(set(normalized_groups)):
        raise TokenCreationError("Grouped token creation groups must be unique")
    host._require_seat(controller, in_game=True)
    token_specs, created_types, created_subtypes, sources = (
        _token_specification_inputs(
            host,
            controller,
            name=name,
            quantity=len(normalized_groups),
            tapped=False,
            attacking=None,
            attacking_groups=normalized_groups,
            battle_protector=None,
            copy_of=copy_of,
            copy_source_zone=copy_source_zone,
            copy_snapshot=copy_snapshot,
            characteristics=characteristics,
            temporary_keywords=temporary_keywords,
            aura_target_ref=None,
        )
    )
    replacement_effects = _token_replacement_effects(
        host,
        controller,
        created_types,
        created_subtypes,
        sources,
    )
    resolved = _resolved_token_specs(
        host,
        controller,
        quantity=len(normalized_groups),
        token_specs=token_specs,
        created_types=created_types,
        created_subtypes=created_subtypes,
        replacement_effects=replacement_effects,
        replacement_selections=replacement_selections,
    )
    cleaned_specs, expanded_groups = _detach_creation_groups(resolved.specs)
    refs = tuple(
        _commit_resolved_token_specs(
            host,
            controller,
            resolved=ResolvedTokenSpecs(
                specs=cleaned_specs,
                journal=resolved.journal,
                remaining_selections=resolved.remaining_selections,
            ),
            base_name=name,
            base_quantity=len(normalized_groups),
            reason=reason,
        )
    )
    if len(refs) != len(expanded_groups):
        raise TokenCreationError(
            "Grouped token identities changed before commit"
        )
    return GroupedTokenCreation(
        refs=refs,
        grouped_refs=tuple(
            (ref, group)
            for ref, group in zip(refs, expanded_groups)
            if group is not None
        ),
    )


def create_distributed_tokens(
    host: TokenCreationHost,
    creators: Sequence[str],
    *,
    token: Mapping[str, Any],
    stack_ref: str,
    reason: str,
    replacement_selections: Sequence[str | None | Mapping[str, Any]] = (),
) -> list[str]:
    """Prepare all creators' replacements and commit one simultaneous entry."""
    from .replacement_effects import ReplacementEventBatch, advance_replacement_batch
    from .semantic_runtime.token_replacements import token_creation_replacement_event
    from .token_copy_runtime import finish_copy_aftercare

    if not creators:
        return []
    apnap = tuple(host.apnap_order())
    if tuple(creators) != tuple(seat for seat in apnap if seat in creators):
        raise TokenCreationError("Token creators must be distinct and in current APNAP order")
    name, quantity = token["name"], token["quantity"]
    events = []
    effects = {}
    for creator in creators:
        host._require_seat(creator, in_game=True)
        specs, types, subtypes, sources = _token_specification_inputs(
            host, creator, name=name, quantity=quantity, tapped=token.get("tapped", False),
            characteristics=token["characteristics"], attacking=None, attacking_groups=(),
            battle_protector=None, copy_of=None, copy_source_zone="battlefield",
            copy_snapshot=None, temporary_keywords=(), aura_target_ref=None,
        )
        events.append(token_creation_replacement_event(
            event_id=f"token.create:distributed:{stack_ref}:{creator}",
            controller=creator, tokens=specs, created_types=types, created_subtypes=subtypes,
        ))
        for effect in _token_replacement_effects(host, creator, types, subtypes, sources):
            if effects.setdefault(effect.effect_id, effect) != effect:
                raise TokenCreationError("Distributed token replacement authority disagrees")
    available_effects = tuple(effects.values())
    progress = advance_replacement_batch(
        ReplacementEventBatch(f"replacement:token.create:distributed:{stack_ref}", tuple(events), apnap),
        available_effects, selections=tuple(replacement_selections), require_all_selections=False,
    )
    if progress.pending is not None:
        raise DistributedTokenChoiceRequired(batch=progress.batch, effects=available_effects, pending=progress.pending)

    creation_timestamp = host.state.timestamp_sequence + 1
    plans: list[PreparedTokenObject] = []
    controllers: dict[str, str] = {}
    creations: list[CommittedTokenCreation] = []
    for event in progress.batch.events:
        creator = event.affected_player
        resolved = _preflight_aura_token_specs(host, creator, event.payload["tokens"])
        group = _prepare_token_objects(
            host, creator, resolved,
            creation_timestamp=creation_timestamp, reference_offset=len(plans),
        )
        objects = tuple(plan.object_id for plan in group)
        plans.extend(group)
        controllers.update({object_id: creator for object_id in objects})
        creations.append(CommittedTokenCreation(
            creator, objects, name, quantity, tuple(_committed_replacement_components(group)),
            tuple(selection for selection in progress.batch.journal if selection.event_id == event.event_id),
        ))
    remaining = replacement_selections[progress.consumed_selections:]
    try:
        entry_plans, prepared_counters, prepared_entries = _prepare_token_entry_counters(
            host, creators[0], plans, remaining, controllers=controllers,
        )
    except ReplacementChoiceRequired as required:
        raise DistributedTokenChoiceRequired(
            batch=required.batch, effects=required.effects, pending=required.pending,
            earlier=public_replacement_labels(progress.batch, available_effects),
        ) from required
    except CounterPlacementError as exc:
        raise TokenCreationError(str(exc)) from exc
    created, _components = _commit_prepared_token_entries(
        host, creators[0], entry_plans, creation_timestamp=creation_timestamp,
        prepared_counters=prepared_counters, prepared_entries=prepared_entries,
        reason=reason, controllers=controllers,
    )
    publish_token_creations(host, creations, reason=reason, prepared_counters=prepared_counters)
    for creation in creations:
        finish_copy_aftercare(host, creation.controller, creation.objects)
    return [host.state.cards[object_id].ref for object_id in created]


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
