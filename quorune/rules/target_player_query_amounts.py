"""Independent target/query/result ownership for a target-player public quantity."""
from collections.abc import Mapping
from ..target_player_query_amount_model import (
    TargetPlayerQueryAmountSpec,TARGET_PLAYER_QUERY_AMOUNT_KIND as KIND,
    TARGET_PLAYER_QUERY_AMOUNT_CAPABILITY as CAP,
)
from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY
from ..characteristic_fragments import characteristic_quantity_capabilities
from ..replacement.immutable import thaw_value


MAIN_PHASE_OPPONENT_MANA_CAPABILITY = "mana.production.once_per_turn_targeted_main_phase"
MAIN_PHASE_OPPONENT_MANA_LIMIT_VERSION = 5
MAIN_PHASE_OPPONENT_MANA_TEMPLATE = "optional-main-phase-opponent-basic-type-mana-v1"
_BASIC_LAND_SUBTYPES = frozenset({"plains", "island", "swamp", "mountain", "forest"})


def main_phase_opponent_mana_condition():
    return {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "main"},
    ]}


def main_phase_opponent_mana_target_schema():
    return {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}


def main_phase_opponent_mana_effect(subtype):
    from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
    from ..object_predicate import ObjectQuerySpec
    from ..query_effect_amount_model import PublicQueryAmountSpec
    from ..variable_resolution_mana_model import MANA_COLORS, VariableResolutionManaChoiceSpec

    if subtype not in _BASIC_LAND_SUBTYPES:
        raise ValueError("Main-phase mana needs one canonical basic land type")
    query = ObjectQuerySpec(zones=("battlefield",), types_all=("land",), subtypes_all=(subtype,))
    quantity = CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE, query=query)
    amount = TargetPlayerQueryAmountSpec(PublicQueryAmountSpec(quantity)).to_dict()
    return VariableResolutionManaChoiceSpec("one_color", tuple(sorted(MANA_COLORS)), amount, optional=True).effect()


def main_phase_opponent_mana_requirements(effects, schema):
    from ..resolution_mana_model import RESOLUTION_MANA_CHOICE_CAPABILITY
    from ..variable_resolution_mana_model import VariableResolutionManaChoiceSpec, VARIABLE_RESOLUTION_MANA_CAPABILITY

    if len(effects) != 1 or thaw_value(schema) != main_phase_opponent_mana_target_schema():
        return ()
    effect, = effects
    if type(schema["count"]) is not int:
        return ()
    try:
        spec = VariableResolutionManaChoiceSpec.from_effect(effect)
        amount = TargetPlayerQueryAmountSpec.from_dict(spec.amount)
        subtype, = amount.amount.quantity.query.subtypes_all
        if thaw_value(effect) != main_phase_opponent_mana_effect(subtype):
            return ()
    except (TypeError, ValueError, KeyError):
        return ()
    return tuple(sorted({
        MAIN_PHASE_OPPONENT_MANA_CAPABILITY, RESOLUTION_MANA_CHOICE_CAPABILITY, VARIABLE_RESOLUTION_MANA_CAPABILITY,
        "trigger.placement.apnap", "trigger.usage.once_per_turn_optional_result", "target.revalidate_resolution",
        *target_player_query_value_dependencies(effect["amount"]),
    }))


def has_main_phase_opponent_mana(program):
    from ..variable_resolution_mana_model import is_optional_target_mana_choice

    return any(is_optional_target_mana_choice(effect) for effect in program.effects)


def main_phase_opponent_mana_program_requirements(program):
    if (
        (program.event, program.active_zone) != ("step.begin", "battlefield")
        or thaw_value(program.event_condition) != main_phase_opponent_mana_condition()
        or thaw_value(program.trigger_limit) != {
            "schema_version": MAIN_PHASE_OPPONENT_MANA_LIMIT_VERSION, "kind": "optional_result_once_per_turn",
        }
        or program.cost_schema is not None or program.handlers
    ):
        raise ValueError("Optional opponent mana requires its original main-phase trigger and success limit")
    required = main_phase_opponent_mana_requirements(program.effects, program.target_schema)
    if not required:
        raise ValueError("Optional opponent mana requires one legal opponent, subtype count and one color choice")
    return required


def target_player_query_value_dependencies(value):
    if not isinstance(value,Mapping) or value.get('kind')!=KIND:return ()
    spec=TargetPlayerQueryAmountSpec.from_dict(value)
    return tuple(sorted({CAP,PUBLIC_QUERY_AMOUNT_CAPABILITY,*characteristic_quantity_capabilities(spec.amount.quantity)}))


def _player_schema_is_closed(schema):
    raw=thaw_value(schema)
    return isinstance(raw,Mapping) and type(raw.get('count')) is int and any(raw=={
        'zones':['player'],'categories':['player'],'player_relation':relation,'count':1}
        for relation in ('any','opponent'))


def target_player_query_node_capabilities(*,effects,target_schema,mechanic_ids=(),**kwargs):
    if len(effects)!=1 or not _player_schema_is_closed(target_schema):return ()
    from ..variable_resolution_mana_model import is_optional_target_mana_choice
    effect=effects[0]
    if is_optional_target_mana_choice(effect):
        return main_phase_opponent_mana_requirements(effects, target_schema)
    op=effect.get('op');slot={'draw':'count','life':'delta'}.get(op)
    value=effect.get(slot) if slot else None
    if not isinstance(value,Mapping) or value.get('kind')!=KIND:return ()
    try:
        spec=TargetPlayerQueryAmountSpec.from_dict(value);projected={**effect,slot:2*spec.amount.coefficient}
        recipient=effect.get('player')
        if recipient not in ('$controller','$target.0'):return ()
        frame=target_schema if recipient=='$target.0' else None
        if op=='draw':
            from .node_capability_shapes import fixed_draw_node_capabilities
            base=fixed_draw_node_capabilities(effects=(projected,),target_schema=frame,
                mechanic_ids=('cr-121-drawing-a-card','cr-115-targets'))
        else:
            from .fixed_controller_effect_shapes import fixed_life_node_capabilities
            base=fixed_life_node_capabilities(effects=(projected,),target_schema=frame,
                mechanic_ids=('cr-119-life','cr-115-targets'))
        if not base:return ()
        return tuple(sorted({*target_player_query_value_dependencies(value),'target.revalidate_resolution',*base}))
    except (ValueError,TypeError,KeyError):return ()


def target_player_query_program_dependencies(program):
    if has_main_phase_opponent_mana(program):
        return main_phase_opponent_mana_program_requirements(program)
    found=[effect for effect in program.effects if isinstance(effect,Mapping)
        and any(isinstance(value,Mapping) and value.get('kind')==KIND for value in effect.values())]
    if not found:return ()
    if not ((program.event=='resolve' and program.active_zone=='stack' and str(program.ability_id).startswith('spell:'))
            or (program.event=='permanent.enter.self' and program.active_zone=='battlefield')):
        raise ValueError('Target public query requires its represented spell or self-entry result frame')
    required=target_player_query_node_capabilities(effects=program.effects,target_schema=program.target_schema)
    if not required:raise ValueError('Target public query requires one player target and a complete ordinary result')
    return required


def is_closed_target_player_query_program(program):
    try:required=target_player_query_program_dependencies(program)
    except (ValueError,TypeError):return False
    return bool(required) and set(required)<=set(program.capability_dependencies)
