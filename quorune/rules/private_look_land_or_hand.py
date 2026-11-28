"""Closed private look, optional tapped land and mandatory hand fallback."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects
from .library_selection import LibrarySelectionObjectIdentity


PRIVATE_LOOK_LAND_OR_HAND_VERSION = 34
PRIVATE_LOOK_LAND_OR_HAND_CAPABILITY = "library.select.private_land_or_hand_random_bottom"
PRIVATE_LOOK_LAND_OR_HAND_MECHANIC = "look-land-or-hand-random-bottom"


def private_look_land_or_hand_effect(count, *, player="$controller"):
    if type(count) is not int or not 2 <= count <= 5:
        raise ValueError("The private look requires a fixed count between two and five")
    return {"op": "fixed_library_selection", "schema_version": PRIVATE_LOOK_LAND_OR_HAND_VERSION,
            "player": player, "look_count": count}


def validate_private_land_hand_start(effect, *, player="$controller"):
    count = effect.get("look_count") if isinstance(effect, Mapping) else None
    if (not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int
            or FrozenMap(effect) != FrozenMap(private_look_land_or_hand_effect(count, player=player))):
        raise ValueError("The private look instruction has a closed field set")
    return count


def private_land_hand_continuation(effect, *, player, stack_ref):
    fields = {"_land_hand_stage", "_land_hand_stack", "_land_hand_looked"}
    source = {key: value for key, value in effect.items() if key not in fields}
    count = validate_private_land_hand_start(source, player=player)
    looked = effect.get("_land_hand_looked")
    stage = effect.get("_land_hand_stage")
    if (type(stack_ref) is not str or not stack_ref or effect.get("_land_hand_stack") != stack_ref
            or stage not in ("land", "hand") or not isinstance(looked, (tuple, list)) or not 1 <= len(looked) <= count):
        raise ValueError("The branch must retain the original resolving private look")
    identities = tuple(LibrarySelectionObjectIdentity.from_dict(raw) for raw in looked)
    if len({row.ref for row in identities}) != len(identities):
        raise ValueError("Looked-at cards must be unique original identities")
    return count, stage, identities


def private_land_hand_capabilities(effect):
    try:
        validate_private_land_hand_start(effect)
    except (TypeError, ValueError):
        return ()
    return ("library.select.fixed_controller", PRIVATE_LOOK_LAND_OR_HAND_CAPABILITY,
            "zone.change.destination_replacement", "library.select.battlefield_controller")


def private_land_hand_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if effect.get("op") == "fixed_library_selection"
                   and effect.get("schema_version") == PRIVATE_LOOK_LAND_OR_HAND_VERSION)
    if not marked and PRIVATE_LOOK_LAND_OR_HAND_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or tuple(program.effects) != marked or program.handlers or program.cost_schema is not None
            or program.event_condition is not None or program.target_schema is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or PRIVATE_LOOK_LAND_OR_HAND_MECHANIC not in program.coverage):
        raise ValueError("The land or hand selection requires the complete original resolving spell")
    dependencies = private_land_hand_capabilities(marked[0])
    if not dependencies:
        raise ValueError("The original private land or hand descriptor is malformed")
    return dependencies


def is_closed_private_land_hand_program(program):
    try:
        required = private_land_hand_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def bind_private_land_hand(host, item, effect):
    if effect.get("op") != "fixed_library_selection" or effect.get("schema_version") != PRIVATE_LOOK_LAND_OR_HAND_VERSION:
        return effect
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_private_land_hand_program(program)
            or item.kind not in {"spell", "spell_copy"} or item.targets
            or item.context.get("currently_resolving") is not True):
        raise ValueError("Private land or hand selection requires its original resolving spell")
    if FrozenMap(effect) == FrozenMap(program.effects[0]):
        return effect
    count, stage, _ = private_land_hand_continuation(effect, player=item.controller, stack_ref=item.ref)
    if stage != "hand" or count != program.effects[0]["look_count"]:
        raise ValueError("The fallback must retain its original printed private look")
    return effect
