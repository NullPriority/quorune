"""Validate a sealed upkeep quantity and read only the participant’s public count."""
from collections.abc import Mapping
from ..entry_designations import ENTRY_DESIGNATION_CAPABILITY, is_linked_opponent_upkeep_condition
from ..query_effect_amount_model import PublicQueryAmountError
from ..upkeep_hand_difference_model import (
    UPKEEP_HAND_DIFFERENCE_CAPABILITY,
    UPKEEP_HAND_DIFFERENCE_ORIGIN,
    HandDifferenceDirection,
    UpkeepHandDifferenceSpec,
)

CONTROLLER_HAND_SURPLUS = UpkeepHandDifferenceSpec(HandDifferenceDirection.HAND_MINUS_CONSTANT, 4)


def controller_upkeep_hand_surplus_condition():
    return {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "upkeep"},
    ]}


def _is_controller_upkeep(condition):
    if not isinstance(condition, Mapping) or set(condition) != {"all"}:
        return False
    clauses = condition["all"]
    return isinstance(clauses, (tuple, list)) and tuple(clauses) == tuple(
        controller_upkeep_hand_surplus_condition()["all"]
    )


def _difference_specs(program):
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_KIND,ScalarEffectAmountSpec
    def visit(value):
        if isinstance(value,Mapping):
            if value.get('kind')==SCALAR_AMOUNT_KIND and value.get('origin')==UPKEEP_HAND_DIFFERENCE_ORIGIN:
                yield ScalarEffectAmountSpec.from_dict(value)
            else:
                for child in value.values():yield from visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:yield from visit(child)
    return tuple(visit((program.effects,getattr(program,'handlers',()))))


def upkeep_hand_difference_program_dependencies(program):
    from .scheduled_player_shapes import SCHEDULED_PLAYER_CAPABILITY,is_upkeep_participant_schedule
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY,ScalarEffectAmountSpec
    specs=_difference_specs(program)
    if not specs:return ()
    schedule = program.event_condition
    linked = is_linked_opponent_upkeep_condition(schedule)
    controller_upkeep = _is_controller_upkeep(schedule)
    if linked and any(spec.hand_difference.schema_version != 1 for spec in specs):
        raise ValueError("The linked upkeep only owns the closed hand differences")
    if (len(specs)!=1 or getattr(program,'event',None)!='step.begin' or getattr(program,'active_zone',None)!='battlefield'
            or getattr(program,'target_schema',False) is not None or getattr(program,'handlers',()) or len(program.effects)!=1
            or not (linked or controller_upkeep or is_upkeep_participant_schedule(schedule))
            or len(schedule['all'])!=2):
        raise ValueError('Hand difference requires its single nontargeted upkeep frame')
    effect=program.effects[0]
    if controller_upkeep:
        if (getattr(program, "cost_schema", None) is not None
                or not isinstance(effect, Mapping) or set(effect) != {"op", "player", "delta"}
                or effect["op"] != "life" or effect["player"] != "$context.player"
                or ScalarEffectAmountSpec.from_dict(effect["delta"]).hand_difference != CONTROLLER_HAND_SURPLUS):
            raise ValueError("Controller hand surplus requires one sealed upkeep-player life result")
        return (UPKEEP_HAND_DIFFERENCE_CAPABILITY, SCALAR_AMOUNT_CAPABILITY,
                SCHEDULED_PLAYER_CAPABILITY, "life.change.effect")
    if (not isinstance(effect,Mapping) or set(effect)!={'op','source','target','amount'}
            or effect['op']!='damage' or effect['source']!='$source' or effect['target']!='$context.player'
            or ScalarEffectAmountSpec.from_dict(effect['amount'])!=specs[0]):
        raise ValueError('Hand difference requires one sealed upkeep-player source-damage result')
    return (UPKEEP_HAND_DIFFERENCE_CAPABILITY,SCALAR_AMOUNT_CAPABILITY,SCHEDULED_PLAYER_CAPABILITY,
            'damage.amount.positive','damage.result.player_life',
            *((ENTRY_DESIGNATION_CAPABILITY,) if linked else ()))


def is_closed_upkeep_hand_difference_program(program):
    try:required=upkeep_hand_difference_program_dependencies(program)
    except (ValueError,TypeError,KeyError):return False
    return bool(required) and set(required)<=set(program.capability_dependencies)


def resolve_upkeep_hand_difference(host,item,difference):
    program=host.semantics.get(item.semantic_key)
    context=item.context;seat=context.get('player')
    schedule = program.event_condition if program is not None else None
    linked = is_linked_opponent_upkeep_condition(schedule)
    if (program is None or not upkeep_hand_difference_program_dependencies(program)
            or item.kind!='triggered_ability' or item.targets or context.get('event')!='step.begin'
            or context.get('step')!='upkeep' or type(seat) is not str or seat not in host.state.players
            or not linked and (seat == item.controller) != _is_controller_upkeep(schedule)):
        raise PublicQueryAmountError('Hand difference lost its sealed upkeep participant')
    player=host.state.players[seat]
    if _difference_specs(program)[0].hand_difference!=difference:
        raise PublicQueryAmountError('Hand difference changed its sealed quantity descriptor')
    if not player.in_game:return 0
    if host.state.step!='upkeep' or host.state.active_player!=seat or not isinstance(player.zones.get('hand'),list):
        raise PublicQueryAmountError('Hand difference requires its current live upkeep participant')
    return difference.evaluate(len(player.zones['hand']))
