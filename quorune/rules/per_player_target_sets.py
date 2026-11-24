"""Independent authority for per-player selection and controller retention."""
from collections.abc import Mapping
from ..replacement.immutable import FrozenMap
from ..linked_exile_return_model import (
    ANNOUNCED_OPPONENT_EXILE_CAPABILITY, LINKED_EXILE_RETURN_MECHANIC, is_announced_opponent_exile,
)
from ..token_copy_recipes import TOKEN_COPY_BATCH_FIELD, TokenCopyBatchSpec
from ..compiler.per_player_target_sets import PER_PLAYER_TARGET_CAPABILITY,PER_PLAYER_TARGET_MECHANIC
from ..compiler.any_number_target_sets import ANY_NUMBER_TARGET_MECHANIC
from ..compiler.fixed_homogeneous_target_sets import FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC
from .any_number_target_shapes import any_number_target_node_capabilities
from .fixed_homogeneous_target_set_capability_shapes import _OPERATION_MECHANICS
from .opponent_exile_draw_results import OPPONENT_EXILE_DRAW_FIELD, opponent_exile_draw_payload_dependencies


def per_player_target_node_capabilities(*,effects,target_schema,mechanic_ids):
    if not isinstance(target_schema,Mapping):return ()
    if (target_schema.get('distinct_controllers') is not True or target_schema.get('retain_controller') is not True
            or target_schema.get('zones')!=['battlefield'] or target_schema.get('categories')!=['permanent']
            or target_schema.get('controller_relation','any') not in {'any','opponent'}):return ()
    if any(is_announced_opponent_exile(effect) for effect in effects):
        from .linked_exile_return_shapes import linked_exile_return_node_capabilities

        return linked_exile_return_node_capabilities(
            effects=effects, target_schema=target_schema,
            mechanic_ids=(LINKED_EXILE_RETURN_MECHANIC, "cr-115-targets"),
        )
    if len(effects) == 1 and TOKEN_COPY_BATCH_FIELD in effects[0]:
        try:
            spec = TokenCopyBatchSpec.from_effect(effects[0])
        except (TypeError, ValueError):
            return ()
        if spec.uses_announced_models and FrozenMap(target_schema) == FrozenMap(spec.target_schema):
            return spec.capability_dependencies
        return ()
    schema={key:value for key,value in target_schema.items() if key not in {'distinct_controllers','retain_controller'}}
    draw = ()
    if len(effects) == 1 and OPPONENT_EXILE_DRAW_FIELD in effects[0]:
        if schema.get("controller_relation") != "opponent":
            return ()
        try:
            draw = opponent_exile_draw_payload_dependencies(effects[0])
        except (TypeError, ValueError):
            return ()
        effects = ({key: value for key, value in effects[0].items() if key != OPPONENT_EXILE_DRAW_FIELD},)
    owned=any_number_target_node_capabilities(effects=effects,target_schema=schema,
        mechanic_ids=set(mechanic_ids)-{PER_PLAYER_TARGET_MECHANIC})
    return tuple(sorted({PER_PLAYER_TARGET_CAPABILITY, *owned, *draw})) if owned else ()


def is_closed_per_player_target_program(program):
    required=per_player_target_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)


def per_player_target_runtime_dependencies(program):
    schema=program.target_schema
    def constrained(value):
        if isinstance(value,Mapping):
            return bool({'distinct_controllers','retain_controller'}&set(value)) or any(constrained(child) for child in value.values())
        return isinstance(value,(list,tuple)) and any(constrained(child) for child in value)
    announced_copy = any(
        isinstance(effect.get(TOKEN_COPY_BATCH_FIELD), Mapping)
        and effect[TOKEN_COPY_BATCH_FIELD].get("schema_version") == 2
        for effect in program.effects
    )
    from .effect_shape_walkers import nested_effects

    handlers = getattr(program, "handlers", ())
    instructions = tuple(nested_effects((program.effects, handlers)))
    announced_exile = any(is_announced_opponent_exile(effect) for effect in instructions)
    draw_outcome = any(OPPONENT_EXILE_DRAW_FIELD in effect for effect in instructions)
    if not constrained(schema) and not announced_copy and not announced_exile and not draw_outcome:
        return ()
    if draw_outcome and (
        len(program.effects) != 1 or OPPONENT_EXILE_DRAW_FIELD not in program.effects[0]
        or handlers or getattr(program, "cost_schema", None) is not None
    ):
        raise ValueError("Opponent exile draws require one closed root target-set result")
    inferred={ANY_NUMBER_TARGET_MECHANIC,FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC,'cr-115-targets'}
    inferred.update(_OPERATION_MECHANICS[effect['op']] for effect in program.effects if effect.get('op') in _OPERATION_MECHANICS)
    required=per_player_target_node_capabilities(effects=program.effects,target_schema=schema,mechanic_ids=inferred)
    if not required:raise ValueError('Per-player targets lack their closed domain, cardinality or primitive')
    if announced_exile and (ANNOUNCED_OPPONENT_EXILE_CAPABILITY not in required
            or program.event != "permanent.enter.self" or program.active_zone != "battlefield"
            or program.event_condition is not None or program.cost_schema is not None or program.handlers):
        raise ValueError("Announced opponent exile requires its exact self-entry source-departure pair")
    return required
