"""Closed end-turn redirection to a chosen permanent or the Aura's creature."""

from ..attachment_references import AttachmentReferenceKind, AttachmentReferenceSpec
from ..damage_modifier_state import (
    CHOSEN_REDIRECTION_DESTINATION as CHOSEN,
    CONTROLLER_REDIRECTION_DESTINATION_TYPES as DESTINATION_TYPES,
    ENCHANTED_REDIRECTION_DESTINATION as ENCHANTED,
    TARGETED_CREATURE_REDIRECTION_DESTINATION as TARGETED,
)
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects

CAPABILITY = "damage.redirection.controller_scope_until_cleanup"
MECHANIC = "controller-damage-redirection"
CHOICE_FIELD = "controller_damage_redirection_choice"
REDIRECTION_FIELD = "controller_damage_redirection"
ATTACHMENT_CAPABILITY = "object.reference.source_attachment_information"
WATCHED_DEATH_CAPABILITY = "trigger.delayed.targeted_death_counter_draw"


def creature_redirection_target_schema(*, optional=False):
    return {
        "categories": ["permanent"],
        "zones": ["battlefield"],
        "types_any": ["creature"],
        "controller_relation": "you",
        **({"up_to": 1} if optional else {"count": 1}),
    }


def redirection_instruction(destination=ENCHANTED):
    if destination == ENCHANTED:
        destination_ref = AttachmentReferenceSpec(
            AttachmentReferenceKind.ENCHANTED, "creature", schema_version=2,
        ).to_dict()
    elif destination == TARGETED:
        destination_ref = "$target.0"
    else:
        destination_ref = "$selected.0"
    return {
        "op": "create_damage_redirection",
        REDIRECTION_FIELD: destination,
        "source": "$source",
        "destination": destination_ref,
        "consume_on_application": False,
    }


def redirection_choice():
    return {
        "op": "choose_objects",
        CHOICE_FIELD: 1,
        "selector": {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "types_any": ["creature", "planeswalker"],
            "controller_relation": "you",
        },
        "minimum": 1,
        "maximum": 1,
        "prompt": "Choose a creature or planeswalker you control.",
        "then": [],
    }


def redirection_payload_dependencies(value):
    if CHOICE_FIELD in value:
        if (thaw_value(value) != redirection_choice()
                or any(type(value[key]) is not int for key in (CHOICE_FIELD, "minimum", "maximum"))):
            raise ValueError("Controlled damage redirection requires one exact resolution choice")
        return (CAPABILITY,)
    if value.get(REDIRECTION_FIELD) == TARGETED:
        if thaw_value(value) != redirection_instruction(TARGETED) or value["consume_on_application"] is not False:
            raise ValueError("Targeted damage redirection requires its exact controlled creature reference")
        return CAPABILITY, WATCHED_DEATH_CAPABILITY, "target.revalidate_resolution"
    if REDIRECTION_FIELD in value:
        expected = redirection_instruction()
        if (thaw_value(value) != expected or value["consume_on_application"] is not False
                or type(value["destination"]["schema_version"]) is not int):
            raise ValueError("Attached damage redirection requires its exact creature reference")
        return CAPABILITY, ATTACHMENT_CAPABILITY
    return ()


def redirection_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    if not any(CHOICE_FIELD in effect or REDIRECTION_FIELD in effect for effect in nested_effects(effects)):
        return None
    if len(effects) != 1 or cost_schema is not None or MECHANIC not in mechanics:
        return ()
    try:
        required = redirection_payload_dependencies(effects[0])
        if WATCHED_DEATH_CAPABILITY in required:
            if thaw_value(target_schema) != creature_redirection_target_schema() or type(target_schema["count"]) is not int:
                return ()
        elif target_schema is not None:
            return ()
        return required
    except (TypeError, ValueError):
        return ()


def redirection_program_dependencies(program):
    required = redirection_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanics=program.coverage, cost_schema=program.cost_schema,
    )
    if (required is None and CAPABILITY not in program.capability_dependencies
            and not any(CHOICE_FIELD in effect or REDIRECTION_FIELD in effect
                        for effect in nested_effects(program.handlers))):
        return ()
    spell = program.event == "resolve" and program.active_zone == "stack" and program.ability_id.startswith("spell:")
    aura = program.event == "permanent.enter.self" and program.active_zone == "battlefield"
    if not required or program.handlers or not (aura if ATTACHMENT_CAPABILITY in required else spell):
        raise ValueError("Controller damage redirection requires its complete spell or source-entry trigger")
    return required


def is_closed_redirection_program(program):
    try:
        required = redirection_program_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
