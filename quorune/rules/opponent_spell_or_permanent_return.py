"""Exact owner-hand movement for the represented spell-or-permanent scopes."""

from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


CAPABILITY = "zone.move.opponent_spell_or_nonland_owner_hand"
FIELD = "opponent_spell_or_nonland_return"
SUBJECT = "opponent-spell-or-nonland-permanent"
ENTRY_CAPABILITY = "zone.move.entry_spell_or_permanent_owner_hand"
ENTRY_FIELD = "entry_spell_or_permanent_return"
ENTRY_SUBJECT = "entry-spell-or-permanent"
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


def opponent_spell_or_permanent_return_capabilities(instruction, schema):
    if (type(instruction.get(ENTRY_FIELD)) is int and thaw_value(instruction) == entry_effect()
            and thaw_value(schema) == entry_target_schema()):
        return (ENTRY_CAPABILITY, *REQUIREMENTS[1:])
    if (type(instruction.get(FIELD)) is int and thaw_value(instruction) == effect()
            and thaw_value(schema) == target_schema()):
        return REQUIREMENTS
    return ()


def opponent_spell_or_permanent_return_runtime_dependencies(program):
    marked = tuple(row for row in nested_effects((program.effects, program.handlers)) if FIELD in row or ENTRY_FIELD in row)
    if not marked and not {CAPABILITY, ENTRY_CAPABILITY}.intersection(program.capability_dependencies):
        return ()
    from_entry = len(marked) == 1 and ENTRY_FIELD in marked[0]
    event, zone = ("permanent.enter.self", "battlefield") if from_entry else ("resolve", "stack")
    if (len(marked) != 1 or tuple(program.effects) != marked or program.handlers or program.event != event
            or program.active_zone != zone or from_entry and program.event_condition is not None):
        raise ValueError("Spell-or-permanent return requires its exact spell or entry event")
    required = opponent_spell_or_permanent_return_capabilities(marked[0], program.target_schema)
    if not required:
        raise ValueError("Spell-or-permanent return requires its exact single target scope")
    return required
