from __future__ import annotations

"""Independent branch-by-branch modal capability traversal."""

from typing import Any, Iterable, Mapping, Sequence

from ..event_counter_reference import (
    attached_attack_counter_context_matches,
    uses_modal_event_counter_recipient,
)
from ..library_order_model import GRAVEYARD_LIBRARY_ORDER_OPERATION
from ..compiler.modal_templates import (
    FIXED_CHOOSE_ONE_MODAL_MECHANIC, FIXED_NONREPEATING_MODAL_MECHANIC,
    FIXED_CHOOSE_ONE_MODAL_CAPABILITY, FIXED_NONREPEATING_MODAL_CAPABILITY,
)
from .modal_capability_shapes import fixed_choose_one_modal_branches, fixed_nonrepeating_modal_branches
from .modal_selection import (
    DISTINCT_TURN_TRIGGER_MODES_CAPABILITY,
    COMMANDER_CAST_BOTH_MODES_CAPABILITY,
    COMMANDER_CAST_MODE_LIMIT_FIELD,
    WIZARD_CAST_TWO_MODES_CAPABILITY,
    OPTIONAL_COMBAT_TRIGGER_MODAL_CAPABILITY,
    distinct_turn_trigger_context_matches,
    has_distinct_turn_trigger_modes,
    valid_distinct_turn_trigger_modes,
    conditional_cast_mode_capability,
    has_optional_trigger_modes,
    optional_combat_trigger_context_matches,
)


def modal_schema_runtime_dependencies(schema, *, allow_pawprint_draw=False):
    """Derive current mode/branch authority without a producer annotation."""
    if not isinstance(schema,Mapping) or not isinstance(schema.get('modes'),Mapping):return ()
    definitions=tuple(schema['modes'].values())
    indexed=tuple(schema['modes'])==tuple(f'mode_{i}' for i in range(1,len(definitions)+1))
    current=('min_modes' in schema and indexed) or any(isinstance(d,Mapping) and 'mechanics' in d for d in definitions)
    if not current:return ()
    modal=FIXED_NONREPEATING_MODAL_MECHANIC if 'min_modes' in schema else FIXED_CHOOSE_ONE_MODAL_MECHANIC
    mechanics={modal}
    for definition in definitions:
        if not isinstance(definition,Mapping) or not isinstance(definition.get('mechanics'),(list,tuple)):
            raise ValueError('Current modal schema lacks its closed branch mechanics')
        mechanics.update(definition['mechanics'])
    required = fixed_modal_capability_dependencies(
        effects=(), target_schema=schema, mechanics=tuple(sorted(mechanics)),
        allow_pawprint_draw=allow_pawprint_draw,
    )
    if not required:raise ValueError('Current modal schema has an unowned branch or malformed cardinality')
    return required


def _temporary_land_mana_mode_dependencies(program, schema):
    from .temporary_ability_grant import OPERATION, TEMPORARY_LAND_MANA_GRANT_CAPABILITY

    modes = schema["modes"].values()
    uses_land_grant = any(
        isinstance(mode, Mapping)
        and any(isinstance(group, Mapping) and "land" in group.get("types_any", ())
                for group in mode.get("groups", ()))
        and any(isinstance(effect, Mapping) and effect.get("op") == OPERATION
                for effect in mode.get("effects", ()))
        for mode in modes
    )
    if not uses_land_grant and TEMPORARY_LAND_MANA_GRANT_CAPABILITY not in program.capability_dependencies:
        return ()
    if any(not isinstance(mode, Mapping) or not isinstance(mode.get("mechanics"), (list, tuple))
           for mode in modes):
        raise ValueError("A modal land mana grant requires closed mode branches")
    mechanics = tuple(dict.fromkeys((
        FIXED_NONREPEATING_MODAL_MECHANIC, *(mechanic for mode in modes for mechanic in mode["mechanics"]),
    )))
    required = fixed_modal_capability_dependencies(effects=program.effects, target_schema=schema, mechanics=mechanics)
    if TEMPORARY_LAND_MANA_GRANT_CAPABILITY not in required:
        raise ValueError("A modal land mana grant requires its exact targeted fixed-output branch")
    return required


def attached_attack_modal_counter_dependencies(program):
    """Require attacher authority for a modal branch that names its event object."""
    if not uses_modal_event_counter_recipient(program.target_schema):
        return ()
    if not attached_attack_counter_context_matches(
        event=program.event, active_zone=program.active_zone,
        event_condition=program.event_condition,
    ):
        raise ValueError("A modal attack counter requires the original attached-attacker occurrence")
    return (
        "attachment.reference.current_or_lki",
        "trigger.effect.fixed_event",
        "trigger.event.normalized_public_action",
        "trigger.placement.apnap",
    )


def modal_event_runtime_dependencies(program):
    """Retain primitive event authority when current modal coverage is removed."""
    from .conditional_target_preparation import resolution_target_color_program_dependencies

    required = set(resolution_target_color_program_dependencies(program))
    from .kicked_modal_capabilities import kicked_modal_runtime_dependencies
    required.update(kicked_modal_runtime_dependencies(program))
    from .escalate_tap_capabilities import escalate_tap_runtime_dependencies
    required.update(escalate_tap_runtime_dependencies(program))
    from .pawprint_capabilities import pawprint_runtime_dependencies
    required.update(pawprint_runtime_dependencies(program))
    required.update(attached_attack_modal_counter_dependencies(program))
    schema=program.target_schema
    if has_distinct_turn_trigger_modes(schema):
        if (program.effects or program.handlers or program.cost_schema is not None
                or not valid_distinct_turn_trigger_modes(schema)
                or not distinct_turn_trigger_context_matches(
                    event=program.event, active_zone=program.active_zone, event_condition=program.event_condition,
                )):
            raise ValueError("Distinct turn modes require the closed controlled creature-entry trigger")
        required.update((DISTINCT_TURN_TRIGGER_MODES_CAPABILITY, "trigger.effect.fixed_event",
                         "trigger.event.normalized_zone_change", "trigger.placement.apnap"))
    elif DISTINCT_TURN_TRIGGER_MODES_CAPABILITY in program.capability_dependencies:
        raise ValueError("Distinct turn mode authority requires the versioned mode schema")
    conditional = isinstance(schema, Mapping) and COMMANDER_CAST_MODE_LIMIT_FIELD in schema
    if conditional:
        capability = conditional_cast_mode_capability(schema)
        if (
            capability is None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or program.effects or program.handlers or program.cost_schema is not None
        ):
            raise ValueError("Cast-conditioned modes require their whole resolving spell")
        required.add(capability)
    elif {COMMANDER_CAST_BOTH_MODES_CAPABILITY, WIZARD_CAST_TWO_MODES_CAPABILITY}.intersection(program.capability_dependencies):
        raise ValueError("Cast-conditioned mode authority requires a cast mode condition")
    if has_optional_trigger_modes(schema):
        if (program.effects or program.handlers or program.cost_schema is not None
                or not optional_combat_trigger_context_matches(
                    event=program.event, active_zone=program.active_zone, event_condition=program.event_condition,
                )):
            raise ValueError("Zero-through-all modes require the original self combat damage to a player trigger")
        required.update((OPTIONAL_COMBAT_TRIGGER_MODAL_CAPABILITY, "trigger.effect.fixed_event",
                         "trigger.event.normalized_damage", "trigger.placement.apnap"))
    elif OPTIONAL_COMBAT_TRIGGER_MODAL_CAPABILITY in program.capability_dependencies:
        raise ValueError("Optional combat trigger mode authority requires the zero-through-all mode schema")
    if not isinstance(schema,Mapping) or not isinstance(schema.get('modes'),Mapping):return tuple(sorted(required))
    required.update(_temporary_land_mana_mode_dependencies(program, schema))
    if 'min_modes' not in schema and not any(isinstance(d,Mapping) and 'mechanics' in d for d in schema['modes'].values()):return tuple(sorted(required))
    from .fixed_resolution_characteristic_shapes import is_equipped_stat_mode_effect

    if ((program.event, program.active_zone) != ("activate", "battlefield")
            and any(is_equipped_stat_mode_effect(effect)
                    for definition in schema["modes"].values() if isinstance(definition, Mapping)
                    for effect in definition.get("effects", ()) if isinstance(effect, Mapping))):
        raise ValueError("A modal Equipment stat modifier requires a battlefield activation")
    from .event_subscriptions import FixedEventSubscriptionSet
    subscribed=FixedEventSubscriptionSet.from_condition(program.event_condition)
    events={s.event for s in subscribed.subscriptions} if subscribed else {program.event}
    if 'spell.cast' in events:required.add('trigger.event.normalized_spell_cast')
    if events.intersection({'permanent.tap','permanent.untap','creature.blocks','creature.becomes_blocked'}):
        required.add('trigger.event.normalized_public_action')
    return tuple(sorted(required))


def fixed_modal_capability_dependencies(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanics: Iterable[str],
    allow_pawprint_draw: bool = False,
) -> tuple[str, ...]:
    from .bound_effect_program_shapes import has_target_controller_recipient, has_target_controller_search_role
    from .capabilities import capability_dependencies_for_node, capability_covered_mechanics, MECHANIC_CAPABILITY_DEPENDENCIES

    mechanic_values = tuple(str(value).casefold() for value in mechanics)
    nonrepeating = FIXED_NONREPEATING_MODAL_MECHANIC in mechanic_values
    branches = (
        fixed_nonrepeating_modal_branches(
            effects=effects,
            target_schema=target_schema,
            mechanic_ids=mechanic_values,
        )
        if nonrepeating
        else fixed_choose_one_modal_branches(
            effects=effects,
            target_schema=target_schema,
            mechanic_ids=mechanic_values,
        )
    )
    # The Pawprint cast path binds the draw recipient to each selected target slot.
    if branches is None or any(
        has_target_controller_search_role(effect)
        or (has_target_controller_recipient(effect) and not (allow_pawprint_draw and effect.get("op") == "draw"))
        for branch in branches for effect in branch.effects
    ):
        return ()
    if target_schema.get("max_modes", 1) > 1:
        if any(
            effect.get("op") == GRAVEYARD_LIBRARY_ORDER_OPERATION
            for branch in branches for effect in branch.effects
        ):
            return ()
        # The hand-count scalar reads the sole selected player. A sibling with
        # its own targets requires a separately scoped amount descriptor.
        from ..scalar_effect_amount_model import TARGET_HAND_COUNT_MECHANIC

        if sum(branch.target_schema is not None for branch in branches) > 1 and any(
            TARGET_HAND_COUNT_MECHANIC in branch.mechanics
            and "public-quantity-mana" in branch.mechanics
            for branch in branches
        ):
            return ()
    modal_mechanic = (
        FIXED_NONREPEATING_MODAL_MECHANIC
        if nonrepeating
        else FIXED_CHOOSE_ONE_MODAL_MECHANIC
    )
    dependencies = {
        (
            FIXED_NONREPEATING_MODAL_CAPABILITY
            if nonrepeating
            else FIXED_CHOOSE_ONE_MODAL_CAPABILITY
        )
    }
    if has_optional_trigger_modes(target_schema):
        dependencies.add(OPTIONAL_COMBAT_TRIGGER_MODAL_CAPABILITY)
    if has_distinct_turn_trigger_modes(target_schema):
        dependencies.add(DISTINCT_TURN_TRIGGER_MODES_CAPABILITY)

    from .fixed_resolution_characteristic_shapes import (
        fixed_attached_stat_node_capabilities, is_equipped_stat_mode_effect,
    )

    from .greatest_power_life import greatest_power_life_node_capabilities, uses_greatest_power_life_effect

    if capability := conditional_cast_mode_capability(target_schema):
        dependencies.add(capability)
    represented_mechanics = {modal_mechanic}
    for branch in branches:
        resolver = capability_dependencies_for_node
        if any(is_equipped_stat_mode_effect(effect) for effect in branch.effects):
            if target_schema.get("max_modes", 1) != 1:
                return ()
            resolver = fixed_attached_stat_node_capabilities
        elif any(uses_greatest_power_life_effect(effect) for effect in branch.effects):
            resolver = greatest_power_life_node_capabilities
        branch_dependencies = resolver(
            effects=branch.effects,
            target_schema=branch.target_schema,
            mechanic_ids=branch.mechanics,
        )
        if not branch_dependencies or (
            set(branch.mechanics)
            - set(capability_covered_mechanics(branch_dependencies))
        ):
            return ()
        dependencies.update(branch_dependencies)
        represented_mechanics.update(branch.mechanics)
    outer_mechanics = set(mechanic_values) - represented_mechanics
    for mechanic in outer_mechanics:
        if mechanic == "cr-603-handling-triggered-abilities":
            dependencies.add("trigger.placement.apnap")
            continue
        required = MECHANIC_CAPABILITY_DEPENDENCIES.get(mechanic)
        if not required:
            return ()
        dependencies.update(required)
    return tuple(sorted(dependencies))
