from __future__ import annotations

"""Select the same live layer-four effects for off-board and prospective spell reads."""

from ..characteristic_evaluation import off_battlefield_self_creature_effects, render_layered_type_line
from ..characteristic_read_frames import memoized_characteristic_read
from ..continuous_effects import CharacteristicState, Layer, evaluate_continuous_effects
from ..semantic_runtime.cross_zone_type_additions import (
    CONTROLLED_CHOSEN_CREATURE_SUBTYPE_HANDLER, CROSS_ZONE_CHOSEN_CREATURE_TYPE_HANDLER,
    CROSS_ZONE_TYPE_ADDITION_HANDLER,
)
from ..semantic_runtime.subtype_composite_grants import (
    FOOD_CREATURE_TYPE_OPERATIONS, SUBTYPE_COMPOSITE_GRANT_HANDLER,
)


_CROSS_ZONE_HANDLER_IDS = frozenset({CROSS_ZONE_TYPE_ADDITION_HANDLER, CROSS_ZONE_CHOSEN_CREATURE_TYPE_HANDLER})
_SUBTYPE_ENTRY_HANDLER_IDS = _CROSS_ZONE_HANDLER_IDS | {
    CONTROLLED_CHOSEN_CREATURE_SUBTYPE_HANDLER,
}
_FOOD_ENTRY_HANDLER_IDS = frozenset({SUBTYPE_COMPOSITE_GRANT_HANDLER})


def _scoped_type_addition_effects(host, handler_ids):
    def read():
        if not any(descriptor.get("handler_id") in handler_ids
                   for program in host.semantics.programs_for_event("characteristics.evaluate")
                   for descriptor in program.handlers):
            return ()
        from ..card_programs.runtime import collect_card_program_continuous_effects

        return collect_card_program_continuous_effects(
            host.state, host.semantics, host.semantic_program_is_current_trusted, maximum_layer=Layer.TYPE,
            static_component_resolver=lambda source: (),
            early_static_component_resolver=host._static_component_keys_at_layer,
            copied_static_source_resolver=host._copied_static_source_fragments,
            handler_ids=handler_ids,
        )

    return memoized_characteristic_read(host, ("scoped-type-addition-effects", handler_ids), read)


def battlefield_entry_type_addition_effects(host, controller):
    """Select continuous type additions applicable to the prospective entrant."""
    food = tuple(effect for effect in _scoped_type_addition_effects(host, _FOOD_ENTRY_HANDLER_IDS)
                 if effect.applies.zones == ("battlefield",) and effect.operations == FOOD_CREATURE_TYPE_OPERATIONS)
    controlled = _scoped_type_addition_effects(host, _SUBTYPE_ENTRY_HANDLER_IDS)
    return (*food, *(effect for effect in controlled
        if effect.applies.zones == ("battlefield",) and effect.applies.controller == controller
        and any(operation.op == "add_types" and operation.field == "subtypes" for operation in effect.operations)))


def cross_zone_type_effects_for_card(host, card, *, maximum_layer=None, prospective_controller=None):
    if maximum_layer is not None and maximum_layer < Layer.TYPE:
        return ()
    zone = "stack" if prospective_controller is not None else card.zone
    controller = prospective_controller if prospective_controller is not None else card.controller
    return tuple(effect for effect in _scoped_type_addition_effects(host, _CROSS_ZONE_HANDLER_IDS)
                 if zone in effect.applies.zones
                 and (effect.applies.owner is None or (
                     card.object_kind == "card" and (zone != "stack" or effect.applies.owner != controller)
                 )))




def _has_self_creature_program(host, card):
    from ..off_battlefield_creatures import OFF_BATTLEFIELD_SELF_CREATURE_HANDLER

    return any(
        descriptor.get("handler_id") == OFF_BATTLEFIELD_SELF_CREATURE_HANDLER
        for program in host.semantics.programs_for_oracle(card.oracle_id, active_zone="all", event="continuous")
        for descriptor in program.handlers
    )


def prospective_cross_zone_spell_type_line(
    host, card, seat, selected_type_line=None, *, include_self_characteristics=True,
):
    effects = cross_zone_type_effects_for_card(host, card, prospective_controller=seat)
    base_data = None
    if include_self_characteristics and _has_self_creature_program(host, card):
        base_data = host._effective_card_data(card, maximum_layer=Layer.TEXT)
        own_effects = off_battlefield_self_creature_effects(card, base_data.get("ability_fragments", ()),
                                                           ignore_face_down=True)
        effects = (*effects, *(effect for effect in own_effects if effect.layer is Layer.TYPE))
    if not effects:
        return None
    type_line = selected_type_line
    if type_line is None:
        if base_data is None:
            base_data = host._effective_card_data(card, maximum_layer=Layer.TEXT)
        type_line = str(base_data.get("type_line") or "")
    types, subtypes, supertypes = host._type_parts(type_line)
    base = CharacteristicState(name=card.printed_name, controller=seat,
        card_types={word.title() for word in types}, subtypes={word.title() for word in subtypes},
        supertypes={word.title() for word in supertypes})
    values = evaluate_continuous_effects(base, effects, context={
        "continuous_dependency_order_version": getattr(host.state, "continuous_dependency_order_version", None),
        "zone": "stack", "owner": card.owner, "object_id": card.object_id,
        "logical_object_id": card.logical_object_id, "ref": card.ref, "token": card.is_token,
    }).characteristics
    types_unchanged = {word.casefold() for word in values["card_types"]} == types
    subtypes_unchanged = {word.casefold() for word in values["subtypes"]} == subtypes
    return type_line if types_unchanged and subtypes_unchanged else render_layered_type_line(values)
