"""One performed source sacrifice produces its fixed private search and life trigger."""

from collections.abc import Mapping

from ..basic_land_types import BASIC_LAND_MANA
from ..errors import GameRuleError
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, REFLEXIVE_PAYMENT_CAPABILITY, REFLEXIVE_PAYMENT_MECHANIC,
    ReflexiveTriggerProgram,
)
from ..replacement.immutable import FrozenMap, thaw_value


CAPABILITY = "trigger.reflexive.source_sacrifice_basic_land_search"
FIELD = "source_sacrifice_reflexive_search"
SEARCH_MECHANIC = "fixed-library-search-to-battlefield"
LIFE_MECHANIC = "cr-119-life"
MECHANICS = ("cr-603-handling-triggered-abilities", "fixed-source-sacrifice", REFLEXIVE_PAYMENT_MECHANIC)
_CHILD_MECHANICS = (SEARCH_MECHANIC, LIFE_MECHANIC)
_LIFE = {"op": "life", "player": "$controller", "delta": 1}


def child_program(search):
    return ReflexiveTriggerProgram(
        (search, _LIFE), None, _CHILD_MECHANICS, schema_version=BOUND_REFLEXIVE_PROGRAM_VERSION,
    )


def validate_child(value):
    from .library_search_capability_shapes import fixed_library_search_node_capabilities

    child = ReflexiveTriggerProgram.from_dict(value)
    if (
        child.target_schema is not None or child.schema_version != BOUND_REFLEXIVE_PROGRAM_VERSION
        or child.mechanic_ids != _CHILD_MECHANICS or len(child.effects) != 2
    ):
        raise ValueError("The source-sacrifice reflexive trigger requires the exact search and life clauses")
    search, life = (thaw_value(effect) for effect in child.effects)
    selector = search.get("selector")
    subtypes = selector.get("subtypes_any") if isinstance(selector, Mapping) else None
    if (
        not isinstance(subtypes, list) or len(subtypes) != 3
        or any(type(kind) is not str or kind not in BASIC_LAND_MANA for kind in subtypes)
        or sorted(set(subtypes)) != subtypes
        or selector != {"types": ["land"], "supertypes": ["basic"], "subtypes_any": subtypes}
        or life != _LIFE or type(life["delta"]) is not int
        or search.get("count") != {"minimum": 1, "maximum": 1}
        or any(type(value) is not int for value in search["count"].values())
        or search.get("shuffle_after") is not True or search.get("enters_tapped_override") is not True
        or search.get("destination") != "battlefield"
    ):
        raise ValueError("The reflexive result requires three basic land subtypes, tapped entry, shuffle and one life")
    owned = fixed_library_search_node_capabilities(effects=(search,), target_schema=None, mechanic_ids=_CHILD_MECHANICS)
    if not owned:
        raise ValueError("The reflexive search has no ordinary library-search owner")
    return child, (
        CAPABILITY, REFLEXIVE_PAYMENT_CAPABILITY, "trigger.event.normalized_zone_change",
        "trigger.placement.apnap", "life.change.effect", *owned,
    )


def validate_parent(program):
    if (
        program is None or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None or program.event_condition is not None
        or program.handlers or len(program.effects) != 1 or FIELD not in program.effects[0]
    ):
        raise ValueError("Source-sacrifice search requires the original unqualified self-entry trigger")
    return validate_child(program.effects[0][FIELD])


def admitted_reflexive_sacrifice(host, intent):
    parent = next((item for item in host.state.stack if item.ref == intent.source_stack_ref), None)
    if (
        parent is None or parent.kind != "triggered_ability" or parent.controller != intent.actor
        or parent.source_object_id != intent.object_id or parent.targets
        or parent.context.get("source_logical_object_id") != intent.logical_object_id
        or parent.context.get("currently_resolving") is not True
    ):
        raise GameRuleError("Source-sacrifice search lost its original resolving entry")
    program = host.semantics.get(parent.semantic_key)
    try:
        child, dependencies = validate_parent(program)
        if (
            not host.semantic_program_is_current_trusted(program)
            or not set(dependencies).issubset(program.capability_dependencies)
            or FrozenMap(child.to_dict()) != intent.reflexive_child
        ):
            raise ValueError("Source-sacrifice search lost its admitted reflexive consequence")
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return parent, child
