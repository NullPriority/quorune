from __future__ import annotations
from .damage_death_exile_model import ResolvedDamageDeathExile
from .linked_departure_exile_model import ResolvedLinkedDepartureExile

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence

from .continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from .declaration_rule_effects import (
    ContinuousJournalEffect,
    ResolutionDeclarationRuleEffect,
)
from .as_unblocked_rule import AsUnblockedAssignmentRule
from .toughness_assignment_rule import ResolvedToughnessAssignmentRule
from .block_capacity_rule_model import ResolvedBlockCapacityRule
from .temporary_cast_rule_model import ResolvedCastRestriction
from .source_block_pair_model import ResolvedSourceBlockPair
from .library_search_model import ResolvedLibrarySearchProhibition
from .declaration_fragments import DeclarationRequirementTemplate, DeclarationRestrictionTemplate
from .defender_permission import DefenderAttackPermission
from .object_predicate import ObjectQuerySpec
from .resolution_characteristic_model import FIXED_TARGET_COLOR_ZONES, decode_fixed_target_color
from .next_turn_effect import NEXT_TURN_EFFECT_FIELD
from .object_query import ObjectQueryResult, object_matches_query, object_query_result
from .keyword_prohibition import prepare_resolution_keyword_exclusions
from .source_continuity import SourceContinuitySnapshot
from .source_tap_effect import RUNTIME_SOURCE_CONTINUITY_FIELD, SOURCE_TAP_EFFECT_FIELD
from .source_untap_rule_model import ResolvedSourceUntapRestraint


class ContinuousEffectStateError(ValueError):
    """Persistent CR 611 state could not be constructed safely."""


@dataclass(frozen=True, slots=True)
class ResolutionContinuousComponent:
    """One layer component of a single resolution-created effect."""

    layer: Layer
    sublayer: str
    operations: tuple[ContinuousOperation, ...]

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "layer", Layer(self.layer))
            operations = tuple(self.operations)
        except (TypeError, ValueError) as exc:
            raise ContinuousEffectStateError(
                "Resolution continuous components require typed layers"
            ) from exc
        if not operations or any(
            not isinstance(operation, ContinuousOperation)
            for operation in operations
        ):
            raise ContinuousEffectStateError(
                "Resolution continuous components require typed operations"
            )
        object.__setattr__(self, "operations", operations)


@dataclass(frozen=True, slots=True)
class ResolutionEffectSource:
    stack_ref: str
    object_id: str | None = None
    logical_object_id: str | None = None
    card_ref: str | None = None

    def __post_init__(self) -> None:
        if type(self.stack_ref) is not str or not self.stack_ref:
            raise ContinuousEffectStateError(
                "A resolution-created effect requires its stack identity"
            )
        if (self.object_id is None) != (self.logical_object_id is None):
            raise ContinuousEffectStateError(
                "A resolution source requires both physical and logical IDs"
            )
        for value in (
            self.object_id,
            self.logical_object_id,
            self.card_ref,
        ):
            if value is not None and (type(value) is not str or not value):
                raise ContinuousEffectStateError(
                    "Resolution source identities must be nonempty strings or null"
                )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "stack_ref": self.stack_ref,
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "card_ref": self.card_ref,
        }

    @classmethod
    def from_effect(
        cls, effect: Mapping[str, Any]
    ) -> "ResolutionEffectSource":
        raw = effect.get("_runtime_source")
        if not isinstance(raw, Mapping) or set(raw) != {
            "stack_ref",
            "object_id",
            "logical_object_id",
            "card_ref",
        }:
            raise ContinuousEffectStateError(
                "A resolving continuous effect requires typed source context"
            )
        for field_name in (
            "stack_ref",
            "object_id",
            "logical_object_id",
            "card_ref",
        ):
            value = raw[field_name]
            if value is not None and (type(value) is not str or not value):
                raise ContinuousEffectStateError(
                    f"Resolution source {field_name} must be a nonempty string or null"
                )
        return cls(
            stack_ref=raw["stack_ref"],
            object_id=raw["object_id"],
            logical_object_id=raw["logical_object_id"],
            card_ref=raw["card_ref"],
        )


def resolution_effect_source(
    host: ContinuousEffectStateHost,
    effect: Mapping[str, Any],
    *,
    fallback_card: Any | None = None,
) -> ResolutionEffectSource:
    """Use typed stack context, with an explicit arbiter/direct-call fallback."""

    if "_runtime_source" in effect:
        return ResolutionEffectSource.from_effect(effect)
    if fallback_card is not None:
        return ResolutionEffectSource(
            stack_ref=f"direct:{fallback_card.ref}",
            object_id=fallback_card.object_id,
            logical_object_id=fallback_card.logical_object_id,
            card_ref=fallback_card.ref,
        )
    return ResolutionEffectSource(
        stack_ref=(
            f"direct:{host.state.turn_sequence}:"
            f"{host.state.event_sequence}:{effect.get('op') or 'effect'}"
        )
    )


class ContinuousEffectStateHost(Protocol):
    state: Any

    @property
    def active_seats(self) -> Sequence[str]: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _next_zone_timestamp(self) -> int: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


def commit_continuous_effect(
    state: Any,
    effect: ContinuousJournalEffect,
) -> ContinuousJournalEffect:
    """Append one validated effect through the canonical journal owner."""

    if not isinstance(
        effect,
        (ContinuousEffect, ResolutionDeclarationRuleEffect, AsUnblockedAssignmentRule, ResolvedToughnessAssignmentRule, ResolvedBlockCapacityRule, ResolvedCastRestriction, ResolvedSourceBlockPair, ResolvedLibrarySearchProhibition, ResolvedDamageDeathExile, ResolvedSourceUntapRestraint,
         ResolvedLinkedDepartureExile),
    ):
        raise ContinuousEffectStateError(
            "Continuous-effect commits require a typed effect"
        )
    journal = state.continuous_effects
    if journal is None:
        raise ContinuousEffectStateError(
            "Continuous-effect state is unavailable"
        )
    _require_expiry_player(state, getattr(effect, "expires_on_next_turn_of", None))
    if any(current.effect_id == effect.effect_id for current in journal):
        raise ContinuousEffectStateError(
            "Continuous-effect identity is already committed"
        )
    journal.append(effect)
    return effect


def _require_expiry_player(state: Any, player: str | None) -> None:
    if player is not None and (player not in state.players or not state.players[player].in_game):
        raise ContinuousEffectStateError("A new next-turn effect requires its live resolving controller")


def _resolution_duration(state: Any, duration: ContinuousEffectDuration, expiry_player: str | None) -> ContinuousEffectDuration:
    if expiry_player is None:
        return duration
    if duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN or state.continuous_effects is None:
        raise ContinuousEffectStateError("Next-turn expiry requires the resolution effect journal")
    _require_expiry_player(state, expiry_player)
    return ContinuousEffectDuration.UNTIL_NEXT_TURN


def matching_battlefield_objects(
    host: ContinuousEffectStateHost,
    predicate: ObjectQuerySpec,
) -> tuple[Any, ...]:
    """Snapshot the current CR 611.2c affected set using effective facts."""

    if not isinstance(predicate, ObjectQuerySpec):
        raise ContinuousEffectStateError(
            "Continuous-effect selection requires ObjectQuerySpec"
        )
    if predicate.zones and predicate.zones != ("battlefield",):
        raise ContinuousEffectStateError(
            "Represented resolution-created characteristic effects use the battlefield"
        )
    matches: list[Any] = []
    for seat in host.active_seats:
        for object_id in tuple(
            host.state.players[seat].zones["battlefield"]
        ):
            card = host.state.cards[object_id]
            effective = host._effective_card_data(card)
            types, subtypes, supertypes = host._type_parts(
                str(effective.get("type_line") or "")
            )
            row = object_query_result(
                card,
                effective,
                type_parts=(types, subtypes, supertypes),
                known_to_actor=True,
                attached_to_ref=(
                    host.state.cards[card.attached_to].ref
                    if card.attached_to in host.state.cards
                    else None
                ),
            )
            if object_matches_query(row, predicate):
                matches.append(card)
    return tuple(matches)


def _characteristic_effect_timing(host, effect, *, actor, source):
    from .resolution_characteristic_model import INDEFINITE_SOURCE_CHARACTERISTIC_VERSIONS

    if effect.get("schema_version") in INDEFINITE_SOURCE_CHARACTERISTIC_VERSIONS:
        return ContinuousEffectDuration.ZONE_OBJECT, None, None
    if SOURCE_TAP_EFFECT_FIELD not in effect:
        expiry = actor if NEXT_TURN_EFFECT_FIELD in effect else None
        return ContinuousEffectDuration.UNTIL_END_OF_TURN, expiry, None
    from .control_effects import prepare_tapped_source_duration

    facts = effect.get(RUNTIME_SOURCE_CONTINUITY_FIELD)
    if not isinstance(facts, Mapping) or set(facts) != {"history", "resolution_timestamp"}:
        raise ContinuousEffectStateError("Tapped-source effect requires authoritative resolution facts")
    timestamp = facts["resolution_timestamp"]
    if type(timestamp) is not int or timestamp < 0:
        raise ContinuousEffectStateError("Tapped-source resolution timestamp is malformed")
    raw_history = facts["history"]
    history = SourceContinuitySnapshot.from_dict(raw_history) if raw_history is not None else None
    active = prepare_tapped_source_duration(host.state, source, history, timestamp)
    if active is None:
        return None
    return ContinuousEffectDuration.UNTIL_SOURCE_LEAVES_OR_UNTAPS, None, active


def apply_fixed_resolution_characteristics(
    host: Any, effect: Mapping[str, Any], *, actor: str, reason: str,
) -> list[str]:
    """Commit a versioned fixed result through the existing multi-layer owner."""
    from .resolution_characteristic_model import (
        DerivedResolutionCharacteristicsSpec, RECIPIENT_MANA_VALUE, fixed_resolution_characteristic_instruction,
        FixedResolutionCardTypeAdditionSpec,
    )
    from .errors import GameRuleError

    instruction = {key: value for key, value in effect.items()
                   if key not in {'_runtime_source', RUNTIME_SOURCE_CONTINUITY_FIELD}}
    if instruction.get('card',True) is None:
        # The canonical semantic source/target resolver pins incarnations and
        # clears a stale reference before this instruction reaches its owner.
        instruction['card']='stale-resolved-reference'
        try:
            fixed_resolution_characteristic_instruction(instruction, resolved=True)
        except (TypeError,ValueError,KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        return []
    try:
        spec, selection = fixed_resolution_characteristic_instruction(instruction, resolved=True)
        if isinstance(selection, ObjectQuerySpec):
            from .rules.resolution_characteristic_shapes import fixed_resolution_characteristic_set_is_closed
            if not fixed_resolution_characteristic_set_is_closed(selection):
                raise GameRuleError('Fixed characteristic set query is unavailable')
            affected = matching_battlefield_objects(host,selection)
        else:
            try:
                affected = (host._resolve_object(actor,selection,zones={'battlefield'}),)
            except GameRuleError:
                return []
        source = resolution_effect_source(host, effect, fallback_card=affected[0] if affected else None)
        timing = ((spec.duration, None, None) if isinstance(spec, FixedResolutionCardTypeAdditionSpec)
                  else _characteristic_effect_timing(host, effect, actor=actor, source=source))
        if timing is None:
            return []
        duration, expiry_player, history = timing
        if isinstance(spec, DerivedResolutionCharacteristicsSpec):
            from .public_quantity_reductions import public_mana_value

            amount = (public_mana_value(host._effective_card_data(affected[0], maximum_layer=Layer.COPY))
                      if spec.quantity == RECIPIENT_MANA_VALUE else spec.quantity)
            spec = spec.materialize(amount)
        components = tuple(ResolutionContinuousComponent(*value) for value in spec.layer_operations())
        create_resolution_continuous_effect_components(
            host, source=source, targets=affected, components=components, duration=duration,
            expires_on_next_turn_of=expiry_player, duration_history=history,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    refs = [card.ref for card in affected]
    if duration is ContinuousEffectDuration.ZONE_OBJECT:
        duration_label = "for this battlefield incarnation"
    elif history is not None:
        duration_label = "until the source untaps or leaves the battlefield"
    else:
        duration_label = "until your next turn" if expiry_player is not None else "until end of turn"
    host._log(actor,'permanent.characteristics',f'Fixed characteristics changed {duration_label}.',
              {'objects':refs,'reason':reason},importance=1,changed_objects=[card.object_id for card in affected])
    return refs


def apply_power_toughness_switch(host, effect, *, actor, reason):
    from .errors import GameRuleError
    from .rules.power_toughness_switch import decode_switch_recipient

    try:
        selection = decode_switch_recipient({key: value for key, value in effect.items()
            if key != "_runtime_source"}, resolved=True)
        if isinstance(selection, ObjectQuerySpec):
            cards = matching_battlefield_objects(host, selection)
        else:
            refs = selection if isinstance(selection, tuple) else (selection,)
            present = []
            for ref in refs:
                try:
                    card = host._resolve_object(actor, ref, zones={"battlefield"})
                except GameRuleError:
                    continue
                if not card.phased_out:
                    present.append(card)
            cards = tuple(present)
        if not cards:
            return []
        create_resolution_continuous_effect_components(
            host, source=resolution_effect_source(host, effect, fallback_card=cards[0]), targets=cards,
            components=(ResolutionContinuousComponent(Layer.POWER_TOUGHNESS, "7d",
                (ContinuousOperation("switch_power_toughness"),)),),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    refs = [card.ref for card in cards]
    host._log(actor, "permanent.characteristics", "Switched power and toughness until end of turn.",
        {"objects": refs, "reason": reason}, importance=1, changed_objects=[card.object_id for card in cards])
    return refs


def apply_target_characteristics(host,effect,*,actor,reason):
    from .rules.target_characteristic_sets import decode_target_characteristics
    from .errors import GameRuleError
    instruction={k:v for k,v in effect.items() if k not in {'_runtime_source', RUNTIME_SOURCE_CONTINUITY_FIELD}}
    try:keywords=decode_target_characteristics(instruction,allow_zero_result=True)
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    refs=instruction['cards']
    if not isinstance(refs,(list,tuple)) or any(type(r) is not str or not r for r in refs) or len(set(refs))!=len(refs) or len(refs)>instruction['maximum_targets']:
        raise GameRuleError('Target characteristic references are malformed')
    cards=tuple(host._resolve_object(actor,ref,zones={'battlefield'}) for ref in refs)
    try:
        source = resolution_effect_source(host, effect, fallback_card=cards[0] if cards else None)
        timing = _characteristic_effect_timing(host, effect, actor=actor, source=source)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    if timing is None:
        return ()
    duration, expiry_player, history = timing
    components=[]
    if keywords:components.append(ResolutionContinuousComponent(Layer.ABILITY,'6',tuple(ContinuousOperation('add_ability',k) for k in keywords)))
    if instruction['power'] or instruction['toughness'] or not keywords:
        components.append(ResolutionContinuousComponent(Layer.POWER_TOUGHNESS,'7c',(ContinuousOperation('modify_power_toughness',(instruction['power'],instruction['toughness'])),)))
    create_resolution_continuous_effect_components(host, source=source,
        targets=cards, components=tuple(components), duration=duration,
        expires_on_next_turn_of=expiry_player, duration_history=history)
    host._log(actor,'permanent.target_characteristics',reason,{'objects':list(refs),'reason':reason},importance=1,changed_objects=[c.object_id for c in cards])
    return tuple(refs)


def apply_fixed_target_color(host, effect, *, actor, reason):
    from .errors import GameRuleError

    try:
        reference, colors, duration = decode_fixed_target_color(effect, materialized=True)
        source = ResolutionEffectSource.from_effect(effect)
    except (ValueError, TypeError) as exc:
        raise GameRuleError(str(exc)) from exc
    item = next((candidate for candidate in host.state.stack if candidate.ref == reference), None)
    if item is None:
        card = host._resolve_object(actor, reference, zones={"battlefield"})
    else:
        card = host.state.cards.get(item.card_object_id or "")
        if item.kind not in {"spell", "spell_copy"} or card is None or card.zone != "stack":
            raise GameRuleError("Fixed target color requires a current spell or permanent")
    operation = ContinuousOperation("set_colors", colors) if colors else ContinuousOperation("remove_all_colors")
    applied = create_resolution_continuous_effect(host, source=source, targets=(card,),
        layer=Layer.COLOR, sublayer="5", operations=(operation,), duration=duration,
        zones=FIXED_TARGET_COLOR_ZONES)
    if applied is None:
        raise GameRuleError("Fixed target color requires the continuous-effect journal")
    host._log(actor, "effect.color", f"The color of {reference} changed.",
        {"target": reference, "colors": list(colors), "duration": duration.value},
        changed_objects=[card.object_id], importance=1)
    return applied


def create_resolution_continuous_effect(
    host: ContinuousEffectStateHost,
    *,
    source: ResolutionEffectSource,
    targets: Sequence[Any],
    layer: Layer,
    sublayer: str,
    operations: Sequence[ContinuousOperation],
    duration: ContinuousEffectDuration = (
        ContinuousEffectDuration.UNTIL_END_OF_TURN
    ),
    expires_on_next_turn_of: str | None = None,
    zones: tuple[str, ...] = ("battlefield",),
) -> ContinuousEffect | None:
    """Commit one immutable effect whose current affected set is locked."""

    duration = _resolution_duration(host.state, duration, expires_on_next_turn_of)
    journal = host.state.continuous_effects
    if journal is None:
        return None
    identities = tuple(
        ContinuousObjectIdentity(
            object_id=card.object_id,
            logical_object_id=card.logical_object_id,
        )
        for card in targets
    )
    if not identities:
        return None
    effect = ContinuousEffect(
        effect_id=host._next_ref("CE"),
        source_id=source.object_id or source.stack_ref,
        layer=layer,
        sublayer=sublayer,
        timestamp=host._next_zone_timestamp(),
        operations=tuple(operations),
        origin=ContinuousEffectOrigin.RESOLUTION,
        duration=duration,
        applies=ObjectQuerySpec(zones=zones),
        locked_objects=identities,
        expires_on_next_turn_of=expires_on_next_turn_of,
    )
    effect, = prepare_resolution_keyword_exclusions(host, targets, (effect,))
    return commit_continuous_effect(host.state, effect)


def create_resolution_continuous_effect_components(
    host: ContinuousEffectStateHost,
    *,
    source: ResolutionEffectSource,
    targets: Sequence[Any],
    components: Sequence[ResolutionContinuousComponent],
    duration: ContinuousEffectDuration = (
        ContinuousEffectDuration.UNTIL_END_OF_TURN
    ),
    expires_on_next_turn_of: str | None = None,
    duration_history: SourceContinuitySnapshot | None = None,
) -> tuple[ContinuousEffect, ...]:
    """Atomically commit one multi-layer effect with one shared timestamp."""

    duration = _resolution_duration(host.state, duration, expires_on_next_turn_of)
    if not isinstance(source, ResolutionEffectSource):
        raise ContinuousEffectStateError(
            "Resolution continuous components require a typed source"
        )
    values = tuple(components)
    if not values or any(
        not isinstance(value, ResolutionContinuousComponent)
        for value in values
    ):
        raise ContinuousEffectStateError(
            "Resolution continuous component sets must be typed and nonempty"
        )
    if len({(value.layer, value.sublayer) for value in values}) != len(values):
        raise ContinuousEffectStateError(
            "Resolution continuous components require unique layer positions"
        )
    identities = tuple(
        ContinuousObjectIdentity(
            object_id=card.object_id,
            logical_object_id=card.logical_object_id,
        )
        for card in targets
    )
    if not identities:
        return ()
    source_id = source.object_id or source.stack_ref
    duration_source = (
        ContinuousObjectIdentity(source.object_id, source.logical_object_id)
        if duration_history is not None else None
    )
    prototypes = tuple(
        ContinuousEffect(
            effect_id=f"resolution-component:{index}",
            source_id=source_id,
            layer=component.layer,
            sublayer=component.sublayer,
            timestamp=0,
            operations=component.operations,
            origin=ContinuousEffectOrigin.RESOLUTION,
            duration=duration,
            applies=ObjectQuerySpec(zones=("battlefield",)),
            locked_objects=identities,
            expires_on_next_turn_of=expires_on_next_turn_of,
            duration_source=duration_source,
            duration_history=duration_history,
        )
        for index, component in enumerate(values, start=1)
    )
    journal = host.state.continuous_effects
    if journal is None:
        raise ContinuousEffectStateError(
            "Continuous-effect state is unavailable"
        )
    timestamp = host._next_zone_timestamp()
    base_ref = host._next_ref("CE")
    effects = tuple(
        replace(
            prototype,
            effect_id=f"{base_ref}:{index}",
            timestamp=timestamp,
        )
        for index, prototype in enumerate(prototypes, start=1)
    )
    effect_ids = {effect.effect_id for effect in effects}
    if any(current.effect_id in effect_ids for current in journal):
        raise ContinuousEffectStateError(
            "Continuous-effect identity is already committed"
        )
    effects = prepare_resolution_keyword_exclusions(host, targets, effects)
    journal.extend(effects)
    return effects


def create_resolution_declaration_rule_effect(
    host: ContinuousEffectStateHost,
    *,
    source: ResolutionEffectSource,
    targets: Sequence[Any],
    restriction: DeclarationRestrictionTemplate | DeclarationRequirementTemplate | DefenderAttackPermission,
    duration: ContinuousEffectDuration = (
        ContinuousEffectDuration.UNTIL_END_OF_TURN
    ),
    live_query: ObjectQuerySpec | None = None,
    expires_on_next_turn_of: str | None = None,
    forbid_activations: bool = False,
) -> ResolutionDeclarationRuleEffect | None:
    """Commit a declaration rule with locked recipients or one live query."""

    duration = _resolution_duration(host.state, duration, expires_on_next_turn_of)
    journal = host.state.continuous_effects
    if journal is None:
        return None
    identities = tuple(
        ContinuousObjectIdentity(
            object_id=card.object_id,
            logical_object_id=card.logical_object_id,
        )
        for card in targets
    )
    if not identities and live_query is None:
        return None
    effect = ResolutionDeclarationRuleEffect(
        effect_id=host._next_ref("DR"),
        source_id=source.object_id or source.stack_ref,
        timestamp=host._next_zone_timestamp(),
        restriction=restriction,
        duration=duration,
        locked_objects=identities,
        live_query=live_query,
        expires_on_next_turn_of=expires_on_next_turn_of,
        forbid_activations=forbid_activations,
    )
    committed = commit_continuous_effect(host.state, effect)
    if not isinstance(committed, ResolutionDeclarationRuleEffect):
        raise ContinuousEffectStateError(
            "Declaration-rule commit returned the wrong effect type"
        )
    return committed


def active_resolution_effects(
    state: Any, card: Any
) -> tuple[ContinuousEffect, ...]:
    from .control_effects import tapped_source_duration_is_current

    journal = state.continuous_effects
    if not journal:
        return ()
    identity = ContinuousObjectIdentity(
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
    )
    return tuple(
        effect
        for effect in journal
        if isinstance(effect, ContinuousEffect)
        and identity in effect.locked_objects
        and (effect.duration_source is None or effect.layer is Layer.CONTROL
             or tapped_source_duration_is_current(state, effect.duration_source, effect.duration_history))
        and (effect.attachment_duration is None
            or effect.attachment_duration.is_current(state.cards))
    )


_PAID_HASTE_ZONES = ("battlefield", "stack")
_PAID_HASTE_OPERATIONS = (ContinuousOperation("add_ability", "Haste"),)


def commit_paid_mana_haste(host, item, card):
    from .errors import GameRuleError
    from .mana_spend_rules import consume_paid_mana_haste

    try:
        lifetime = consume_paid_mana_haste(host.state.players[item.controller].stats)
    except ValueError as exc:
        raise GameRuleError("Paid haste lifetime is malformed") from exc
    if lifetime is None:
        return
    if item.kind != "spell" or item.card_object_id != card.object_id or card.zone != "stack":
        raise GameRuleError("Paid haste requires its cast spell incarnation")
    source = ResolutionEffectSource(item.ref, card.object_id, card.logical_object_id, card.ref)
    effect = create_resolution_continuous_effect(host, source=source, targets=(card,),
        layer=Layer.ABILITY, sublayer="6", operations=_PAID_HASTE_OPERATIONS,
        duration=ContinuousEffectDuration(lifetime.value), zones=_PAID_HASTE_ZONES)
    if effect is None:
        raise GameRuleError("Paid haste requires the continuous-effect journal")


def _is_paid_mana_haste_effect(effect: ContinuousJournalEffect) -> bool:
    return (isinstance(effect, ContinuousEffect) and effect.origin is ContinuousEffectOrigin.RESOLUTION
            and effect.layer is Layer.ABILITY and effect.operations == _PAID_HASTE_OPERATIONS
            and effect.applies.zones == _PAID_HASTE_ZONES)


def is_fixed_target_color_effect(effect: ContinuousJournalEffect) -> bool:
    return (isinstance(effect, ContinuousEffect) and effect.layer is Layer.COLOR
            and effect.applies is not None and effect.applies.zones == FIXED_TARGET_COLOR_ZONES)


def fixed_target_color_effects(state: Any, card: Any) -> tuple[ContinuousEffect, ...]:
    return tuple(effect for effect in active_resolution_effects(state, card) if is_fixed_target_color_effect(effect))


def stack_resolution_effects(state: Any, card: Any) -> tuple[ContinuousEffect, ...]:
    return tuple(effect for effect in active_resolution_effects(state, card)
                 if is_fixed_target_color_effect(effect) or _is_paid_mana_haste_effect(effect))


def active_resolution_declaration_rule_effects(
    state: Any,
    card: Any,
    *,
    query_row: ObjectQueryResult | None = None,
) -> tuple[ResolutionDeclarationRuleEffect, ...]:
    """Return rules matching this incarnation or its current public query row."""

    journal = state.continuous_effects
    if not journal:
        return ()
    identity = ContinuousObjectIdentity(
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
    )
    return tuple(
        effect
        for effect in journal
        if isinstance(effect, ResolutionDeclarationRuleEffect)
        and (
            identity in effect.locked_objects
            or effect.live_query is not None
            and query_row is not None
            and object_matches_query(query_row, effect.live_query)
        )
    )


def expire_zone_departure_effects(state: Any, card: Any) -> None:
    journal = state.continuous_effects
    if not journal:
        return
    identity = ContinuousObjectIdentity(card.object_id, card.logical_object_id)
    journal[:] = [effect for effect in journal if not (
        isinstance(effect, ResolvedLinkedDepartureExile) and effect.subject == identity
        or (is_fixed_target_color_effect(effect) or _is_paid_mana_haste_effect(effect)) and identity in effect.locked_objects
    )]


def expire_end_of_turn_continuous_effects(state: Any) -> int:
    journal = state.continuous_effects
    if journal is None:
        return 0
    retained = [
        effect
        for effect in journal
        if effect.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN
    ]
    expired = len(journal) - len(retained)
    journal[:] = retained
    return expired


def next_turn_expiry_players(state: Any) -> frozenset[str]:
    return frozenset(
        player for effect in state.continuous_effects or ()
        if (player := getattr(effect, "expires_on_next_turn_of", None)) is not None
    )


def expire_next_turn_continuous_effects(state: Any, player: str) -> None:
    """End the effects anchored to the first eligible turn of this player."""
    journal = state.continuous_effects
    if journal:
        retained = [effect for effect in journal if getattr(effect, "expires_on_next_turn_of", None) != player]
        if len(retained) != len(journal):
            journal[:] = retained


def expire_control_change_continuous_effects(state: Any, card: Any) -> int:
    """End identity-pinned effects at the first control-change boundary."""

    journal = state.continuous_effects
    if journal is None:
        return 0
    identity = ContinuousObjectIdentity(
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
    )
    retained = [
        effect
        for effect in journal
        if not (
            isinstance(effect, ContinuousEffect)
            and effect.duration
            is ContinuousEffectDuration.UNTIL_CONTROL_CHANGE
            and identity in effect.locked_objects
        )
    ]
    expired = len(journal) - len(retained)
    journal[:] = retained
    return expired


__all__ = [
    "ContinuousEffectStateError",
    "ResolutionEffectSource",
    "ResolutionContinuousComponent",
    "active_resolution_declaration_rule_effects",
    "active_resolution_effects",
    "create_resolution_declaration_rule_effect",
    "create_resolution_continuous_effect",
    "create_resolution_continuous_effect_components",
    "expire_control_change_continuous_effects",
    "expire_end_of_turn_continuous_effects",
    "matching_battlefield_objects",
    "resolution_effect_source",
]
