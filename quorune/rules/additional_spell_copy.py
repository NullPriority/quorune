"""Resolve the currently effective static authorities for a spell-copy event."""

from ..entry_copy import copiable_oracle_id
from ..replacement.spell_copy_occurrence import AdditionalSpellCopySource, COPY_EVENT

ADDITIONAL_SPELL_COPY_COMPONENT = "replacement.stack.additional-spell-copy.v1"
ADDITIONAL_SPELL_COPY_CAPABILITY = "stack.copy.additional_spell_copy"


def additional_spell_copy_descriptor():
    return {"handler_id": ADDITIONAL_SPELL_COPY_COMPONENT, "schema_version": 1, "event": COPY_EVENT}


def validate_additional_spell_copy_descriptor(descriptor):
    if dict(descriptor) != additional_spell_copy_descriptor() or type(descriptor["schema_version"]) is not int:
        raise ValueError("An additional spell copy requires its exact static descriptor")


def additional_spell_copy_sources(host, controller):
    """Discover only active, effective and currently trusted abilities of the copier."""
    from ..semantic_runtime.spell_copy_components import default_spell_copy_replacement_components

    player = host.state.players.get(controller)
    if player is None or controller not in host.active_seats:
        return ()
    result = []
    effective = None
    for object_id in player.zones["battlefield"]:
        card = host.state.cards[object_id]
        if card.controller != controller or card.phased_out:
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(
            copiable_oracle_id(card), active_zone="battlefield", event=COPY_EVENT,
        )
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            if effective is None:
                effective = host._effective_static_component_key_map()
            if program.key not in effective.get(object_id, ()):
                continue
            for index, descriptor in enumerate(program.handlers):
                source = AdditionalSpellCopySource(card.logical_object_id, f"{program.key}:{index}", controller)
                result.extend(default_spell_copy_replacement_components().lower(descriptor, source))
    return tuple(result)
