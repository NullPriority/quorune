"""Re-derive source-sacrifice authority from the versioned executable payload."""
from collections.abc import Mapping

from ..source_sacrifice_model import (
    CAST_PARTICIPANT_SACRIFICE_DRAW_VERSION, FORMER_ATTACHMENT_SACRIFICE_VERSION,
    REFLEXIVE_SEARCH_SACRIFICE_VERSION, SOURCE_SACRIFICE_FIELD, SOURCE_SACRIFICE_MECHANIC,
    UNATTACHMENT_EVENT, fixed_source_sacrifice_dependencies,
)


def fixed_source_sacrifice_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or SOURCE_SACRIFICE_MECHANIC not in mechanic_ids:
        return ()
    if len(effects) == 2:
        from ..source_sacrifice_model import fixed_source_sacrifice_effect
        from ..library_search_model import FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION
        from .library_search_capability_shapes import fixed_counted_library_search_node_capabilities

        if (effects[0] != fixed_source_sacrifice_effect() or type(effects[0][SOURCE_SACRIFICE_FIELD]) is not int
                or effects[0]["tapped"] is not False
                or effects[1].get("schema_version") != FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION):
            return ()
        search = fixed_counted_library_search_node_capabilities(
            effects=effects[1:], target_schema=None, mechanic_ids=mechanic_ids,
        )
        return (*fixed_source_sacrifice_dependencies(effects[0]), *search) if search else ()
    if len(effects) != 1:
        return ()
    try:
        return fixed_source_sacrifice_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def uses_opponent_threshold_search(effects):
    from ..library_search_model import FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION

    return any(effect.get("op") == "search"
               and effect.get("schema_version") == FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION for effect in effects)


def uses_fixed_source_sacrifice_sequence(effects):
    return (bool(effects) and SOURCE_SACRIFICE_FIELD in effects[0]
            and (len(effects) == 1 or uses_opponent_threshold_search(effects)))


def _opponent_threshold_search_dependencies(program):
    from ..continuous_conditions import (
        FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, FixedPublicStateConditionKind,
        FixedPublicStateConditionSpec, FixedPublicStateFact,
    )
    from ..player_maximum_model import OPPONENT_CREATURE_THRESHOLD_CAPABILITY
    from ..replacement.immutable import FrozenMap, thaw_value

    if ((program.event, program.active_zone) != ("step.begin", "battlefield")
            or program.cost_schema is not None or program.handlers or not isinstance(program.event_condition, Mapping)):
        raise ValueError("Opponent threshold search requires its original unpriced battlefield upkeep")
    raw = thaw_value(FrozenMap(program.event_condition))
    conditions = raw.get("all")
    schedule = {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "upkeep"},
    ]}
    if set(raw) != {"all"} or not isinstance(conditions, list) or len(conditions) != 2 or conditions[0] != schedule:
        raise ValueError("Opponent threshold search requires its controller's upkeep")
    public = conditions[1]
    if (not isinstance(public, Mapping) or set(public) != {"field", "op", "value", "condition"}
            or public["field"] != FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD
            or public["op"] != "truthy" or public["value"] is not True):
        raise ValueError("Opponent threshold search requires an intervening public condition")
    condition = FixedPublicStateConditionSpec.from_dict(public["condition"])
    if (condition.kind is not FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST
            or condition.fact is not FixedPublicStateFact.OPPONENT_CREATURE_COUNT_MAXIMUM):
        raise ValueError("Opponent threshold search needs its independent per-opponent creature count")
    required = fixed_source_sacrifice_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Opponent threshold search requires its original sacrifice and simultaneous search sequence")
    return (*required, OPPONENT_CREATURE_THRESHOLD_CAPABILITY,
            "trigger.condition.fixed_public_state", "trigger.effect.fixed_event", "trigger.placement.apnap")


def fixed_source_sacrifice_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if uses_opponent_threshold_search(nested_effects((program.effects, program.handlers))):
        return _opponent_threshold_search_dependencies(program)

    required = set()
    versions = set()

    def visit(value):
        if isinstance(value, Mapping):
            required.update(fixed_source_sacrifice_dependencies(value))
            if SOURCE_SACRIFICE_FIELD in value:
                versions.add(value[SOURCE_SACRIFICE_FIELD])
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
    visit((program.effects, program.handlers))
    if required and program.active_zone != 'battlefield':
        raise ValueError('Fixed source sacrifice requires its represented battlefield source')
    if FORMER_ATTACHMENT_SACRIFICE_VERSION in versions and (
        program.event != UNATTACHMENT_EVENT or program.target_schema is not None
        or getattr(program, 'cost_schema', None) is not None or program.handlers or len(program.effects) != 1
        or program.effects[0].get(SOURCE_SACRIFICE_FIELD) != FORMER_ATTACHMENT_SACRIFICE_VERSION
        or program.event_condition != {'field': 'card', 'op': 'eq', 'value': '$source.ref'}
    ):
        raise ValueError('Former attachment sacrifice requires its single untargeted source unattachment frame')
    if CAST_PARTICIPANT_SACRIFICE_DRAW_VERSION in versions and (
        program.event != 'spell.cast' or program.event_condition is not None
        or program.target_schema is not None or getattr(program, 'cost_schema', None) is not None
        or program.handlers or len(program.effects) != 1
        or program.effects[0].get(SOURCE_SACRIFICE_FIELD) != CAST_PARTICIPANT_SACRIFICE_DRAW_VERSION
    ):
        raise ValueError('Source sacrifice draw requires its single unqualified spell-cast frame')
    from .source_sacrifice_reflexive_search import CAPABILITY, validate_parent

    if REFLEXIVE_SEARCH_SACRIFICE_VERSION in versions or CAPABILITY in program.capability_dependencies:
        validate_parent(program)
    return tuple(sorted(required))


def is_closed_fixed_source_sacrifice_program(program):
    required = fixed_source_sacrifice_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    if required and program.effects[0].get(SOURCE_SACRIFICE_FIELD) in {
        CAST_PARTICIPANT_SACRIFICE_DRAW_VERSION, REFLEXIVE_SEARCH_SACRIFICE_VERSION,
    }:
        try:
            fixed_source_sacrifice_runtime_dependencies(program)
        except (TypeError, ValueError):
            return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
