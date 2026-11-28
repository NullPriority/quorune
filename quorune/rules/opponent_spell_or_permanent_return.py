"""Exact owner-hand movement for the represented spell-or-permanent scopes."""

from collections.abc import Mapping

from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


CAPABILITY = "zone.move.opponent_spell_or_nonland_owner_hand"
FIELD = "opponent_spell_or_nonland_return"
SUBJECT = "opponent-spell-or-nonland-permanent"
ENTRY_CAPABILITY = "zone.move.entry_spell_or_permanent_owner_hand"
ENTRY_FIELD = "entry_spell_or_permanent_return"
ENTRY_SUBJECT = "entry-spell-or-permanent"
SPELL_CAPABILITY = "zone.move.stack_spell_owner_hand"
SPELL_FIELD = "stack_spell_owner_hand_return"
SPELL_SUBJECT = "stack-spell"
COPY_RETURN_FIELD = "copied_stack_spell_return"
REQUIREMENTS = (CAPABILITY, "zone.single_owner_move.fixed_destination",
                "target.revalidate_resolution", "zone.change.destination_replacement")


def target_schema():
    return {"zones": ["stack", "battlefield"], "categories": ["spell", "permanent"],
            "types_none": ["land"], "controller": "opponent", "count": 1,
            "source_exclusion": True}


def effect():
    return {"op": "move", "card": "$target.0", "destination": "hand", FIELD: 1}


def entry_target_schema():
    return {"zones":["stack", "battlefield"], "categories":["spell", "permanent"], "count":1}


def entry_effect():
    return {"op":"move", "card":"$target.0", "destination":"hand", ENTRY_FIELD:1}


def spell_return_target_schema():
    return {"zones": ["stack"], "categories": ["spell"], "count": 1}


def spell_return_effect():
    return {"op": "move", "card": "$target.0", "destination": "hand", SPELL_FIELD: 1}


def copy_and_return_spell_effects():
    from ..stack_spell_copy_model import FixedStackSpellCopySpec

    return (*FixedStackSpellCopySpec().effects,
            {"op": "move", "card": "$target.0", "destination": "hand", COPY_RETURN_FIELD: 1})


def copy_and_return_spell_capabilities(effects, schema):
    from ..stack_spell_copy_model import FixedStackSpellCopySpec, STACK_SPELL_COPY_CAPABILITY

    if (not isinstance(schema, Mapping) or len(effects) != 2 or not isinstance(effects[1], Mapping)
            or type(effects[1].get(COPY_RETURN_FIELD)) is not int
            or tuple(thaw_value(effect) for effect in effects) != copy_and_return_spell_effects()
            or type(schema.get("count")) is not int
            or thaw_value(schema) != FixedStackSpellCopySpec().target_schema):
        return ()
    return (STACK_SPELL_COPY_CAPABILITY, *REQUIREMENTS[1:])


def opponent_spell_or_permanent_return_capabilities(instruction, schema):
    if (type(instruction.get(SPELL_FIELD)) is int and thaw_value(instruction) == spell_return_effect()
            and thaw_value(schema) == spell_return_target_schema()):
        return (SPELL_CAPABILITY, *REQUIREMENTS[1:])
    if (type(instruction.get(ENTRY_FIELD)) is int and thaw_value(instruction) == entry_effect()
            and thaw_value(schema) == entry_target_schema()):
        return (ENTRY_CAPABILITY, *REQUIREMENTS[1:])
    if (type(instruction.get(FIELD)) is int and thaw_value(instruction) == effect()
            and thaw_value(schema) == target_schema()):
        return REQUIREMENTS
    return ()


def opponent_spell_or_permanent_return_runtime_dependencies(program):
    fields = {FIELD, ENTRY_FIELD, SPELL_FIELD, COPY_RETURN_FIELD}
    marked = tuple(row for row in nested_effects((program.effects, program.handlers, program.target_schema))
                   if fields.intersection(row))
    if any(COPY_RETURN_FIELD in row for row in marked):
        required = copy_and_return_spell_capabilities(program.effects, program.target_schema)
        if (not required or (program.event, program.active_zone) != ("resolve", "stack")
                or program.handlers or program.event_condition is not None or program.cost_schema is not None):
            raise ValueError("Copy and return requires its exact spell target and ordered results")
        return required
    if not marked and not {CAPABILITY, ENTRY_CAPABILITY, SPELL_CAPABILITY}.intersection(program.capability_dependencies):
        return ()
    from_entry = len(marked) == 1 and ENTRY_FIELD in marked[0]
    event, zone = ("permanent.enter.self", "battlefield") if from_entry else ("resolve", "stack")
    effects = tuple(program.effects)
    trailing_draw = {"op": "draw", "player": "$controller", "count": 1, "private": True}
    if len(marked) == 1 and SPELL_FIELD in marked[0] and len(effects) == 2:
        if (thaw_value(effects[1]) != trailing_draw or type(effects[1].get("count")) is not int
                or effects[1].get("private") is not True):
            raise ValueError("Stack spell return supports only its independent ordinary draw follower")
        effects = effects[:1]
    if (len(marked) != 1 or effects != marked or program.handlers or program.event != event
            or program.active_zone != zone or from_entry and program.event_condition is not None):
        raise ValueError("Spell-or-permanent return requires its exact spell or entry event")
    required = opponent_spell_or_permanent_return_capabilities(marked[0], program.target_schema)
    if not required:
        raise ValueError("Spell-or-permanent return requires its exact single target scope")
    if SPELL_FIELD in marked[0]:
        if program.cost_schema is not None or program.event_condition is not None:
            raise ValueError("Stack spell return requires its original unconditioned spell body")
        if len(program.effects) == 2:
            return (*required, "zone.draw.library_to_hand")
    return required
