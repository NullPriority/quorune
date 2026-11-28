"""Independent public quantity, life-loss relation and source-frame ownership."""
from collections.abc import Mapping
from ..characteristic_fragments import characteristic_quantity_capabilities
from ..replacement.immutable import FrozenMap
from ..per_player_query_life_model import (
    PerPlayerQueryLifeLossSpec,BoundPerPlayerLifeLoss,per_player_query_life_spec,
    PER_PLAYER_QUERY_LIFE_KIND as KIND,BOUND_PER_PLAYER_QUERY_LIFE_KIND as BOUND_KIND,
    PER_PLAYER_QUERY_LIFE_CAPABILITY as CAP,
    OPPONENT_SUBTYPE_ABSENCE_LIFE_VERSION,OPPONENT_POISON_THRESHOLD_LIFE_VERSION,
    OPPONENT_ACTUAL_TURN_LOSS_VERSION,
)
from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY

POISON_THRESHOLD_CAPABILITY = 'life.loss.opponent_poison_counter_threshold'
OPPONENT_ACTUAL_TURN_LOSS_CAPABILITY = 'life.loss.per_opponent_actual_turn_loss'
_EACH_END_STEP = FrozenMap({'all': [{'field': 'step', 'op': 'eq', 'value': 'end_step'}]})


def per_player_query_life_value_dependencies(value):
    if not isinstance(value,Mapping) or value.get('kind') not in {KIND,BOUND_KIND}:return ()
    if value['kind']==BOUND_KIND:
        BoundPerPlayerLifeLoss.from_dict(value)
        return (CAP,PUBLIC_QUERY_AMOUNT_CAPABILITY,'life.change.effect')
    spec=PerPlayerQueryLifeLossSpec.from_dict(value)
    if spec.schema_version == OPPONENT_POISON_THRESHOLD_LIFE_VERSION:
        return (CAP, POISON_THRESHOLD_CAPABILITY, 'life.change.effect')
    if spec.schema_version == OPPONENT_ACTUAL_TURN_LOSS_VERSION:
        return (CAP, OPPONENT_ACTUAL_TURN_LOSS_CAPABILITY, 'life.change.effect')
    return tuple(sorted({CAP,PUBLIC_QUERY_AMOUNT_CAPABILITY,'life.change.effect',
        *characteristic_quantity_capabilities(spec.amount.quantity)}))


def per_player_query_life_node_capabilities(*,effects,target_schema,mechanic_ids=(),cost_schema=None,**kwargs):
    if len(effects)!=1 or target_schema is not None or cost_schema is not None:return ()
    try:per_player_query_life_spec(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    from .fixed_controller_effect_shapes import fixed_life_node_capabilities
    base=fixed_life_node_capabilities(effects=({**effects[0],'amount':1},),target_schema=None,
        mechanic_ids=('cr-119-life','cr-101-the-magic-golden-rules'))
    if not base:return ()
    return tuple(sorted({*base,*per_player_query_life_value_dependencies(effects[0]['amount'])}))


def _contains_life_query(value):
    if isinstance(value,Mapping):
        return value.get('kind') in {KIND,BOUND_KIND} or any(_contains_life_query(v) for v in value.values())
    return isinstance(value,(list,tuple)) and any(_contains_life_query(v) for v in value)


def per_player_query_life_program_dependencies(program):
    extras=(getattr(program,'handlers',()),program.target_schema,getattr(program,'cost_schema',None))
    if _contains_life_query(extras):raise ValueError('Public per-player life quantity cannot be a price, catalog or target descriptor')
    if not _contains_life_query(program.effects):return ()
    effect = program.effects[0] if len(program.effects) == 1 else None
    amount = effect.get('amount') if isinstance(effect, Mapping) else None
    version = amount.get('schema_version') if isinstance(amount, Mapping) and amount.get('kind') == KIND else None
    absence = version == OPPONENT_SUBTYPE_ABSENCE_LIFE_VERSION
    poison = version == OPPONENT_POISON_THRESHOLD_LIFE_VERSION
    history = version == OPPONENT_ACTUAL_TURN_LOSS_VERSION
    if history:
        if ((program.event, program.active_zone) != ('step.begin', 'battlefield')
                or not str(program.ability_id).startswith('trigger:')
                or not isinstance(program.event_condition, Mapping)
                or FrozenMap(program.event_condition) != _EACH_END_STEP):
            raise ValueError('Actual turn life loss requires its untargeted each-end-step frame')
    elif poison:
        if ((program.event, program.active_zone) != ('permanent.tap', 'battlefield')
                or program.event_condition != {'field': 'card', 'op': 'eq', 'value': '$source.ref'}):
            raise ValueError('Poison-qualified life loss requires its normalized source tap')
    elif absence:
        if ((program.event, program.active_zone) != ('creature.attacks', 'battlefield')
                or program.event_condition != {'field': 'card', 'op': 'eq', 'value': '$source.ref'}):
            raise ValueError('Opponent subtype absence requires its normalized source attack')
    elif not ((program.event=='resolve' and program.active_zone=='stack' and str(program.ability_id).startswith('spell:'))
            or (program.event=='permanent.enter.self' and program.active_zone=='battlefield')):
        raise ValueError('Public per-player life loss requires its ordinary spell or self-entry frame')
    if effect is None and all(row.get("op") == "apply_if_public_condition" and row.get("schema_version") == 6
                              for row in program.effects) and program.event == "resolve":
        from .resolution_condition_shapes import resolution_condition_node_capabilities

        required = resolution_condition_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
            cost_schema=getattr(program, "cost_schema", None),
        )
    else:
        required=per_player_query_life_node_capabilities(effects=program.effects,target_schema=program.target_schema,
            cost_schema=getattr(program,'cost_schema',None))
    if not required:raise ValueError('Per-player life loss requires one complete independent quantity and result')
    if history:
        return tuple(sorted({*required, 'trigger.placement.apnap'}))
    if absence:
        return tuple(sorted({*required, 'trigger.event.normalized_self_attack', 'trigger.placement.apnap'}))
    if poison:
        return tuple(sorted({*required, 'trigger.event.normalized_public_action', 'trigger.placement.apnap'}))
    return required


def is_closed_per_player_query_life_program(program):
    try:required=per_player_query_life_program_dependencies(program)
    except (TypeError,ValueError):return False
    return bool(required) and set(required)<=set(program.capability_dependencies)
