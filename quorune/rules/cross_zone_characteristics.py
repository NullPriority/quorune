from __future__ import annotations

"""Select the same live layer-four effects for off-board and prospective spell reads."""

from ..characteristic_evaluation import render_layered_type_line
from ..characteristic_read_frames import memoized_characteristic_read
from ..continuous_effects import CharacteristicState, Layer, evaluate_continuous_effects
from ..semantic_runtime.cross_zone_type_additions import CROSS_ZONE_TYPE_ADDITION_HANDLER


def _cross_zone_type_effects(host):
    def read():
        if not any(descriptor.get("handler_id") == CROSS_ZONE_TYPE_ADDITION_HANDLER
                   for program in host.semantics.programs_for_event("characteristics.evaluate")
                   for descriptor in program.handlers):
            return ()
        from ..card_programs.runtime import collect_card_program_continuous_effects

        return collect_card_program_continuous_effects(
            host.state, host.semantics, host.semantic_program_is_current_trusted, maximum_layer=Layer.TYPE,
            static_component_resolver=lambda source: (),
            early_static_component_resolver=host._static_component_keys_at_layer,
            copied_static_source_resolver=host._copied_static_source_fragments,
            handler_ids=frozenset({CROSS_ZONE_TYPE_ADDITION_HANDLER}),
        )

    return memoized_characteristic_read(host, ("cross-zone-type-addition-effects",), read)


def cross_zone_type_effects_for_card(host, card, *, maximum_layer=None, prospective_controller=None):
    if maximum_layer is not None and maximum_layer < Layer.TYPE:
        return ()
    zone = "stack" if prospective_controller is not None else card.zone
    controller = prospective_controller if prospective_controller is not None else card.controller
    return tuple(effect for effect in _cross_zone_type_effects(host)
                 if zone in effect.applies.zones
                 and (effect.applies.owner is None or (
                     card.object_kind == "card" and (zone != "stack" or effect.applies.owner != controller)
                 )))


def prospective_cross_zone_spell_type_line(host, card, seat, selected_type_line=None):
    effects = cross_zone_type_effects_for_card(host, card, prospective_controller=seat)
    if not effects:
        return None
    type_line = selected_type_line
    if type_line is None:
        type_line = str(host._effective_card_data(card, maximum_layer=Layer.TEXT).get("type_line") or "")
    types, subtypes, supertypes = host._type_parts(type_line)
    base = CharacteristicState(name=card.printed_name, controller=seat,
        card_types={word.title() for word in types}, subtypes={word.title() for word in subtypes},
        supertypes={word.title() for word in supertypes})
    values = evaluate_continuous_effects(base, effects, context={
        "zone": "stack", "owner": card.owner, "object_id": card.object_id,
        "logical_object_id": card.logical_object_id, "ref": card.ref, "token": card.is_token,
    }).characteristics
    return (type_line if {word.casefold() for word in values["card_types"]} == types
            else render_layered_type_line(values))
