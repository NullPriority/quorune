from __future__ import annotations

"""Closed CardProgram capability shapes for single-object tap state."""

from typing import Iterable, Mapping, Sequence

from ..attachment_references import (
    ATTACHMENT_INFORMATION_CAPABILITY,
    AttachmentReferenceError,
    AttachmentReferenceKind,
    AttachmentReferenceSpec,
)
from ..compiler.fixed_source_effect_sequences import SOURCE_ZONE_OBJECT
from ..damage_recipient_model import DAMAGE_PERMANENT_CAPABILITY, DAMAGE_PERMANENT_OBJECT
from ..event_counter_reference import BLOCK_COUNTERPART_OBJECT
from ..replacement.immutable import FrozenMap, thaw_value
from ..source_tap_effect import (
    OPTIONAL_SOURCE_CONTROL_UNTAP_VERSION, SOURCE_CONTROL_UNTAP_CAPABILITY, SOURCE_CONTROL_UNTAP_FIELD, SOURCE_TAP_UNTAP_CAPABILITY,
    split_source_control_untap_restraint, split_source_tap_untap_restraint,
)
from ..tap_state import (
    ATTACKING_CREATURES_NEXT_UNTAP_FIELD, CONTROLLER_UNTAP_STEP_COUNT, TAP_TARGET_SET_UNTAP_FIELD,
    two_controller_untaps_effect, validate_attacking_creatures_next_untap,
)
from ..next_player_untap_model import (
    NextPlayerUntapScope, decode_next_player_untap_instruction, is_next_player_untap_instruction,
    next_player_untap_target_schema,
)
from .effect_shape_walkers import nested_effects
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed,
    direct_target_predicate_capabilities,
)


OPTIONAL_EQUIPPED_TAP_STATE_FIELD = "optional_equipped_tap_state"


def optional_equipped_creature_tap_state_effect() -> dict[str, object]:
    reference = AttachmentReferenceSpec(AttachmentReferenceKind.EQUIPPED, "creature", schema_version=2)
    return {
        "op": "choose_option",
        OPTIONAL_EQUIPPED_TAP_STATE_FIELD: 1,
        "player": "$controller",
        "prompt": "Change the equipped creature's tap state?",
        "options": [
            {"id": "tap", "label": "Tap"},
            {"id": "untap", "label": "Untap"},
            {"id": "decline", "label": "Leave unchanged"},
        ],
        "then_by_choice": {
            "tap": [{"op": "tap", "card": reference.to_dict()}],
            "untap": [{"op": "untap", "card": reference.to_dict()}],
            "decline": [],
        },
    }


def is_optional_equipped_creature_tap_state_effect(effect: Mapping[str, object]) -> bool:
    return (
        type(effect.get(OPTIONAL_EQUIPPED_TAP_STATE_FIELD)) is int
        and FrozenMap(effect) == FrozenMap(optional_equipped_creature_tap_state_effect())
    )


_OPTIONAL_EQUIPPED_TAP_STATE_CAPABILITIES = (
    "attachment.reference.current_or_lki",
    ATTACHMENT_INFORMATION_CAPABILITY,
    "permanent.tap.effect",
    "permanent.tap_state.optional_choice",
    "permanent.untap.effect",
)


def _operation_capability(value: object) -> str | None:
    return {
        "tap": "permanent.tap.effect",
        "untap": "permanent.untap.effect",
        "prohibit_next_untap": "untap.step.prohibit_next_controller",
    }.get(value)


def target_set_next_untap_dependencies(values: object) -> tuple[str, ...]:
    if isinstance(values, Mapping):
        if TAP_TARGET_SET_UNTAP_FIELD in values:
            if values.get("op") != "tap_targets" or values[TAP_TARGET_SET_UNTAP_FIELD] is not True:
                raise ValueError("A next-untap instruction requires a tap target set")
            return ("untap.step.prohibit_next_controller",)
        values = tuple(values.values())
    if isinstance(values, (list, tuple)):
        return tuple(dict.fromkeys(cap for value in values for cap in target_set_next_untap_dependencies(value)))
    return ()


def event_next_untap_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 2 or not isinstance(effects[0], Mapping)
            or 'tap-and-untap' not in mechanic_ids):
        return ()
    reference = effects[0].get('card')
    if reference == DAMAGE_PERMANENT_OBJECT:
        binding = DAMAGE_PERMANENT_CAPABILITY
    elif reference == BLOCK_COUNTERPART_OBJECT:
        binding = 'trigger.event.block_pair_counterpart'
    else:
        return ()
    if tuple(effects) != ({'op': 'tap', 'card': reference}, {'op': 'prohibit_next_untap', 'card': reference}):
        return ()
    return ('permanent.tap.effect', 'untap.step.prohibit_next_controller', binding)


MANA_TAPPED_LAND_UNTAP_VARIANT = "opponent_mana_tapped_land_next_untap"


def mana_tapped_land_untap_condition():
    return {"field": "player", "op": "ne", "value": "$source.controller"}


def mana_tapped_land_untap_effect():
    return {"op": "prohibit_next_untap", "card": "$event.permanent_object"}


def mana_tapped_land_untap_runtime_dependencies(program):
    from ..triggered_mana_model import LAND_MANA_TAP_EVENT

    if program.event != LAND_MANA_TAP_EVENT or tuple(program.effects) != (mana_tapped_land_untap_effect(),):
        return ()
    if (program.active_zone != "battlefield" or program.target_schema is not None
            or program.cost_schema is not None or program.handlers
            or "one_or_more_event_batch" in program.coverage
            or program.event_condition != mana_tapped_land_untap_condition()):
        raise ValueError("The land restraint requires the exact opponent land mana event and its original land")
    return ("untap.step.prohibit_next_controller", "trigger.event.normalized_public_action")


def _next_player_untap_capabilities(effect, target_schema, mechanics):
    try:
        instruction = decode_next_player_untap_instruction(effect)
    except (TypeError, ValueError, KeyError):
        return ()
    if (not {"tap-and-untap", "cr-115-targets"} <= mechanics or instruction.player != "$target.0"
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or target_schema != next_player_untap_target_schema(instruction.scope)):
        return ()
    return ("untap.step.prohibit_next_controller", "target.revalidate_resolution")


def _destroy_creatures_and_controller_untap_capabilities(effects, mechanics):
    from ..affected_permanents import AffectedPermanentSetSpec
    from ..object_predicate import ObjectQuerySpec
    from ..replacement.immutable import thaw_value
    from .destruction_regeneration_capability_shapes import mass_destruction_node_capabilities

    if "tap-and-untap" not in mechanics:
        return ()
    destroy, restriction = effects
    try:
        instruction = decode_next_player_untap_instruction(restriction)
    except (TypeError, ValueError, KeyError):
        return ()
    if instruction.player != "$controller" or instruction.scope is not NextPlayerUntapScope.LANDS:
        return ()
    creature_set = AffectedPermanentSetSpec(ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)))
    if thaw_value(destroy) != {"op": "destroy_all", "source": "$source", "set": creature_set.to_dict()}:
        return ()
    required = mass_destruction_node_capabilities(
        effects=(destroy,), target_schema=None, mechanic_ids=("destroy", "destroy-fixed-set"),
    )
    return (*required, "untap.step.prohibit_next_controller") if required else ()


CONTROL_AND_NEXT_OWN_LANDS_CAPABILITY = "untap.step.prohibit_next_own_lands_after_control"


def _control_and_next_own_lands_capabilities(effects, target_schema, mechanics):
    from .control_capability_shapes import fixed_control_node_capabilities

    if (not {"tap-and-untap", "cr-115-targets"} <= mechanics
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or thaw_value(target_schema) != {"zones": ["battlefield"], "categories": ["permanent"], "count": 1,
                                             "types_any": ["artifact", "creature", "enchantment"]}):
        return ()
    control, restriction = effects
    try:
        instruction = decode_next_player_untap_instruction(restriction)
    except (TypeError, ValueError, KeyError):
        return ()
    if (instruction.player != "$controller" or instruction.scope is not NextPlayerUntapScope.LANDS
            or thaw_value(control) != {"op": "gain_control", "card": "$target.0", "controller": "$controller",
                                       "source": "$source", "duration": "zone_object"}):
        return ()
    owned = fixed_control_node_capabilities(effects=(control,), target_schema=target_schema, mechanic_ids=mechanics)
    return (*owned, CONTROL_AND_NEXT_OWN_LANDS_CAPABILITY, "untap.step.prohibit_next_controller") if owned else ()


def _combat_prevention_attackers_untap_capabilities(effects, target_schema, mechanics):
    if (target_schema is not None or len(effects) != 2
            or not {"tap-and-untap", "cr-615-prevention-effects"} <= mechanics):
        return ()
    try:
        validate_attacking_creatures_next_untap(effects[1])
    except (TypeError, ValueError):
        return ()
    if effects[0] != {
        "op": "create_damage_prevention_shield", "source": "$source", "subject": "*",
        "mode": "all", "duration": "until_end_of_turn", "damage_kind": "combat",
    }:
        return ()
    return ("damage.prevention.persistent_amount", "untap.step.prohibit_next_controller")


def _target_combat_prevention_two_untaps_capabilities(effects, target_schema, mechanics):
    from ..compiler.direct_target import direct_permanent_target_spec

    expected_target = direct_permanent_target_spec("target creature").to_target_schema()
    if (len(effects) != 3 or target_schema != expected_target or type(target_schema["count"]) is not int
            or not {"tap-and-untap", "cr-115-targets", "cr-615-prevention-effects"} <= mechanics):
        return ()
    from ..replacement.immutable import thaw_value
    from .prevention_component_shapes import targeted_outgoing_combat_shield

    tap, prevention, restriction = effects
    if (tap != {"op": "tap", "card": "$target.0"}
            or thaw_value(prevention) != targeted_outgoing_combat_shield()
            or restriction != two_controller_untaps_effect()
            or type(restriction["schema_version"]) is not int
            or type(restriction[CONTROLLER_UNTAP_STEP_COUNT]) is not int):
        return ()
    return ("permanent.tap.effect", "damage.prevention.persistent_amount",
            "untap.step.prohibit_next_controller", "target.revalidate_resolution")


def _uses_combat_player_land_tap(effect):
    from ..affected_permanents import COMBAT_DAMAGE_PLAYER_REFERENCE, COMBAT_PLAYER_LAND_TAP_FIELD

    raw_set = effect.get("set")
    return (COMBAT_PLAYER_LAND_TAP_FIELD in effect
            or effect.get("op") == "set_public_tap_state" and isinstance(raw_set, Mapping)
            and raw_set.get("target_controller") == COMBAT_DAMAGE_PLAYER_REFERENCE)


def _combat_recipient_land_tap_untap_dependencies(program):
    from ..affected_permanents import fixed_controller_land_untap_effect
    from ..replacement.immutable import FrozenMap
    from .fixed_effect_clause_shapes import fixed_effect_clause_sequence_node_capabilities

    condition = {"all": [
        {"field": "source_controller", "op": "eq", "value": "$source.controller"},
        {"field": "source_types", "op": "contains_any", "value": ["creature"]},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
    ]}
    if ((program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or "one_or_more_event_batch" not in program.coverage
            or FrozenMap(program.event_condition or {}) != FrozenMap(condition)
            or len(program.effects) != 2 or not _uses_combat_player_land_tap(program.effects[0])
            or FrozenMap(program.effects[1]) != FrozenMap(fixed_controller_land_untap_effect())):
        raise ValueError("Combat land tap and untap require the original grouped recipient and ordered land sets")
    required = fixed_effect_clause_sequence_node_capabilities(
        effects=program.effects, target_schema=None, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Combat land tap and untap lack their exact current land sets")
    return required


def _optional_pirate_entry_is_closed(program):
    if (
        (program.event, program.active_zone) != ("permanent.enter", "battlefield")
        or len(program.effects) != 1
        or program.effects[0].get(SOURCE_CONTROL_UNTAP_FIELD) != OPTIONAL_SOURCE_CONTROL_UNTAP_VERSION
        or not isinstance(program.event_condition, Mapping)
    ):
        return False
    from ..compiler.fixed_counter_trigger_nodes import FixedCounterZoneController, FixedCounterZoneSubject

    subject = FixedCounterZoneSubject(
        "permanent", FixedCounterZoneController.SOURCE, subtype="pirate", include_source=True,
    )
    return thaw_value(FrozenMap(program.event_condition)) == subject.event_condition


def is_source_untap_counter_sequence(effects, target_schema):
    if target_schema is not None or len(effects) != 2:
        return False
    untap, counter = effects
    return (
        untap == {"op": "untap", "card": SOURCE_ZONE_OBJECT}
        and set(counter) == {"op", "card", "counter", "amount", "source"}
        and counter["op"] == "place_counters" and counter["card"] == SOURCE_ZONE_OBJECT
        and counter["source"] == "$source" and type(counter["counter"]) is str
        and bool(counter["counter"].strip()) and type(counter["amount"]) is int and counter["amount"] > 0
    )


SOURCE_ATTACK_CONDITION = {"field": "card", "op": "eq", "value": "$source.ref"}


def untap_runtime_dependencies(program):
    source_restraint = {"op": "prohibit_next_untap", "card": SOURCE_ZONE_OBJECT}
    if any(
        effect.get("op") == source_restraint["op"] and effect.get("card") == SOURCE_ZONE_OBJECT
        for effect in nested_effects((program.effects, program.handlers))
    ):
        if (
            (program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or program.event_condition != SOURCE_ATTACK_CONDITION
            or program.target_schema is not None
            or program.cost_schema is not None
            or program.handlers
            or program.trigger_limit is not None
            or tuple(program.effects) != (source_restraint,)
        ):
            raise ValueError("The source next-untap restriction requires one unqualified source attack")
        return (
            "untap.step.prohibit_next_controller", "trigger.event.normalized_self_attack",
            "trigger.placement.apnap", "trigger.effect.fixed_event",
        )
    from ..affected_permanents import TARGET_PLAYER_CREATURE_UNTAP_SNAPSHOT_FIELD, TARGET_UNSPENT_MANA_FIELD

    if any(_uses_combat_player_land_tap(effect) for effect in nested_effects((program.effects, program.handlers))):
        return _combat_recipient_land_tap_untap_dependencies(program)
    if any(OPTIONAL_EQUIPPED_TAP_STATE_FIELD in effect
           for effect in nested_effects((program.effects, program.handlers))):
        if ((program.event, program.active_zone) != ("activate", "battlefield")
                or program.target_schema is not None or program.event_condition is not None
                or any(handler.get("handler_id") != "activation.catalog.pinned.v1" for handler in program.handlers)
                or len(program.effects) != 1
                or not is_optional_equipped_creature_tap_state_effect(program.effects[0])):
            raise ValueError("Optional equipped tap state requires the original untargeted activation")
        return _OPTIONAL_EQUIPPED_TAP_STATE_CAPABILITIES
    required = target_set_next_untap_dependencies(program.effects)
    if any(CONTROLLER_UNTAP_STEP_COUNT in effect for effect in nested_effects((program.effects, program.handlers))):
        result = _target_combat_prevention_two_untaps_capabilities(
            program.effects, program.target_schema, set(program.coverage),
        )
        if (not result or (program.event, program.active_zone) != ("resolve", "stack") or program.handlers
                or program.cost_schema is not None or program.event_condition is not None):
            raise ValueError("Two controller untaps require the complete original targeted spell")
        return result
    if any(ATTACKING_CREATURES_NEXT_UNTAP_FIELD in effect
           for effect in nested_effects((program.effects, program.handlers))):
        if ((program.event, program.active_zone) != ("resolve", "stack") or program.handlers
                or program.cost_schema is not None or program.event_condition is not None):
            raise ValueError("Attacking-creature untap restriction requires the original resolving spell")
        attacker_result = _combat_prevention_attackers_untap_capabilities(
            program.effects, program.target_schema, set(program.coverage),
        )
        if not attacker_result:
            raise ValueError("Attacking-creature untap requires the ordered combat prevention and current attacking set")
        return attacker_result
    if nonland_untap := opponent_nonland_untap_runtime_dependencies(program):
        return nonland_untap
    rider_fields = {TARGET_UNSPENT_MANA_FIELD, TARGET_PLAYER_CREATURE_UNTAP_SNAPSHOT_FIELD}
    if any(rider_fields.intersection(effect) for effect in nested_effects((program.effects, program.handlers))):
        from .public_zone_move_capability_shapes import fixed_public_tap_state_set_node_capabilities

        if ((program.event, program.active_zone) != ("resolve", "stack") or program.handlers
                or program.cost_schema is not None or program.event_condition is not None
                or len(program.effects) != 1 or not rider_fields.intersection(program.effects[0])):
            raise ValueError("The target-player tap-set rider requires a single exact resolving spell")
        scoped_result = fixed_public_tap_state_set_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        if not scoped_result:
            raise ValueError("The target-player tap-set rider requires the original player and complete result")
        return scoped_result
    if any(SOURCE_CONTROL_UNTAP_FIELD in effect for effect in nested_effects((program.effects, program.handlers))):
        scoped = targeted_tap_state_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        creature_entry = (program.event, program.active_zone) == ("permanent.enter.self", "battlefield")
        saga_chapter = (
            program.active_zone == "battlefield"
            and program.event in {"saga.chapter.1", "saga.chapter.2"}
            and "saga_chapter_dispatch" in program.coverage
            and len(program.effects) == 1 and program.effects[0].get(SOURCE_CONTROL_UNTAP_FIELD) == 1
            and isinstance(program.target_schema, Mapping) and "colors_any" not in program.target_schema
        )
        original_entry = (creature_entry or saga_chapter) and program.event_condition is None
        if (SOURCE_CONTROL_UNTAP_CAPABILITY not in scoped
                or not (original_entry or _optional_pirate_entry_is_closed(program))
                or program.handlers or program.cost_schema is not None):
            raise ValueError("Controlled-source restraint requires its exact original entry or chapter occurrence")
        return tuple(sorted({*required, *scoped}))
    if not any(is_next_player_untap_instruction(effect) for effect in nested_effects(program.effects)):
        return required
    next_step = targeted_tap_state_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not next_step or (program.event, program.active_zone) != ("resolve", "stack"):
        raise ValueError("A next-player untap restriction requires the exact resolving spell and player scope")
    return tuple(sorted({*required, *next_step}))


def opponent_nonland_tap_capabilities(effect, target_schema, mechanics):
    from ..affected_permanents import (
        TARGET_OPPONENT_NONLAND_UNTAP_CAPABILITY, AffectedPermanentSetError,
        decode_target_opponent_nonland_untap_snapshot, target_opponent_nonland_snapshot_target_schema,
    )
    from ..object_predicate import object_query_capabilities

    if ("cr-115-targets" not in mechanics or type((target_schema or {}).get("count")) is not int
            or target_schema != target_opponent_nonland_snapshot_target_schema()):
        return ()
    try:
        spec = decode_target_opponent_nonland_untap_snapshot(effect)
    except (TypeError, AffectedPermanentSetError):
        return ()
    return (
        TARGET_OPPONENT_NONLAND_UNTAP_CAPABILITY, "permanent.tap_state.fixed_set", "target.revalidate_resolution",
        "untap.step.prohibit_next_controller", *object_query_capabilities(spec.query),
    )


def opponent_nonland_untap_runtime_dependencies(program):
    from ..affected_permanents import TARGET_OPPONENT_NONLAND_UNTAP_SNAPSHOT_FIELD
    from .effect_shape_walkers import nested_effects

    field = TARGET_OPPONENT_NONLAND_UNTAP_SNAPSHOT_FIELD
    if not any(field in effect for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("saga.chapter.2", "battlefield")
            or "saga_chapter_dispatch" not in program.coverage or program.handlers
            or program.cost_schema is not None or program.event_condition is not None
            or len(program.effects) != 1 or field not in program.effects[0]):
        raise ValueError("The nonland untap restriction requires its exact original Saga chapter")
    required = opponent_nonland_tap_capabilities(program.effects[0], program.target_schema, program.coverage)
    if not required:
        raise ValueError("The nonland untap restriction requires its announced opponent and complete snapshot")
    return required


def _controlled_source_untap_target_is_closed(target_schema, *, optional):
    cardinality = "up_to" if optional else "count"
    if not isinstance(target_schema, Mapping) or type(target_schema.get(cardinality)) is not int:
        return False
    original = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], cardinality: 1}
    normalized = thaw_value(FrozenMap(target_schema))
    if optional:
        return normalized == original
    opponent = {**original, "controller_relation": "opponent"}
    return normalized in (opponent, {**opponent, "colors_any": ["G", "R"]})


def targeted_tap_state_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership only for closed single-object tap-state shapes."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if len(effects) == 3:
        return _target_combat_prevention_two_untaps_capabilities(effects, target_schema, mechanics)
    if target_schema is None and len(effects) == 2:
        if isinstance(effects[1], Mapping) and is_next_player_untap_instruction(effects[1]):
            return _destroy_creatures_and_controller_untap_capabilities(effects, mechanics)
        if isinstance(effects[1], Mapping) and ATTACKING_CREATURES_NEXT_UNTAP_FIELD in effects[1]:
            return _combat_prevention_attackers_untap_capabilities(effects, target_schema, mechanics)
        return event_next_untap_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if 'tap-and-untap' in mechanics and len(effects) == 2 and target_schema is not None:
        if is_next_player_untap_instruction(effects[1]):
            return _control_and_next_own_lands_capabilities(effects, target_schema, mechanics)
        if (list(effects) != [{'op': 'tap', 'card': '$target.0'}, {'op': 'prohibit_next_untap', 'card': '$target.0'}]
                or 'cr-115-targets' not in mechanics or not direct_permanent_target_schema_is_closed(target_schema)):
            return ()
        return ('permanent.tap.effect', 'untap.step.prohibit_next_controller',
                *direct_target_predicate_capabilities(target_schema), 'target.revalidate_resolution')
    if "tap-and-untap" not in mechanics or len(effects) != 1:
        return ()
    if target_schema is None and effects[0] == mana_tapped_land_untap_effect():
        return ("untap.step.prohibit_next_controller", "trigger.event.normalized_public_action")
    if is_next_player_untap_instruction(effects[0]):
        return _next_player_untap_capabilities(effects[0], target_schema, mechanics)
    try:
        effect, control_restraint = split_source_control_untap_restraint(effects[0])
        effect, tap_restraint = split_source_tap_untap_restraint(effect)
    except ValueError:
        return ()
    if tap_restraint and target_schema is None:
        return ()
    optional_control_restraint = (
        control_restraint and effects[0].get(SOURCE_CONTROL_UNTAP_FIELD) == OPTIONAL_SOURCE_CONTROL_UNTAP_VERSION
    )
    if control_restraint and not _controlled_source_untap_target_is_closed(
        target_schema, optional=optional_control_restraint,
    ):
        return ()
    operation = effect.get("op")

    if target_schema is None:
        if is_optional_equipped_creature_tap_state_effect(effect):
            return _OPTIONAL_EQUIPPED_TAP_STATE_CAPABILITIES
        capability = _operation_capability(operation)
        if capability is None or set(effect) != {"op", "card"}:
            return ()
        reference = effect.get("card")
        if reference == SOURCE_ZONE_OBJECT:
            return (capability,)
        if not isinstance(reference, Mapping):
            return ()
        try:
            AttachmentReferenceSpec.from_dict(reference)
        except (AttachmentReferenceError, TypeError):
            return ()
        return (capability, "attachment.reference.current_or_lki")

    if (
        "cr-115-targets" not in mechanics
        or (not control_restraint and not direct_permanent_target_schema_is_closed(target_schema))
    ):
        return ()
    target_capabilities = () if optional_control_restraint else direct_target_predicate_capabilities(target_schema)
    capability = _operation_capability(operation)
    if (
        capability is not None
        and set(effect) == {"op", "card"}
        and effect.get("card") == "$target.0"
    ):
        return (
            capability,
            *target_capabilities,
            "target.revalidate_resolution",
            *((SOURCE_TAP_UNTAP_CAPABILITY,) if tap_restraint else ()),
            *((SOURCE_CONTROL_UNTAP_CAPABILITY,) if control_restraint else ()),
        )

    if set(effect) != {
        "op",
        "player",
        "prompt",
        "options",
        "then_by_choice",
    } or operation != "choose_option":
        return ()
    options = effect.get("options")
    then_by_choice = effect.get("then_by_choice")
    choice_rows = (
        {key: list(value) for key, value in then_by_choice.items()}
        if isinstance(then_by_choice, Mapping)
        and all(
            isinstance(value, (list, tuple))
            for value in then_by_choice.values()
        )
        else None
    )
    if (
        effect.get("player") != "$controller"
        or effect.get("prompt")
        != "Tap, untap, or leave the target unchanged."
        or not isinstance(options, (list, tuple))
        or list(options)
        != [
            {"id": "tap", "label": "Tap"},
            {"id": "untap", "label": "Untap"},
            {"id": "decline", "label": "Leave unchanged"},
        ]
        or choice_rows is None
        or set(choice_rows) != {"tap", "untap", "decline"}
        or choice_rows["tap"]
        != [{"op": "tap", "card": "$target.0"}]
        or choice_rows["untap"]
        != [{"op": "untap", "card": "$target.0"}]
        or choice_rows["decline"] != []
    ):
        return ()
    return (
        "permanent.tap.effect",
        "permanent.tap_state.optional_choice",
        "permanent.untap.effect",
        *target_capabilities,
        "target.revalidate_resolution",
    )


__all__ = ["event_next_untap_node_capabilities", "targeted_tap_state_node_capabilities", "target_set_next_untap_dependencies"]
