"""Validate a sealed opponent-upkeep quantity and read only its public count."""
from collections.abc import Mapping
from ..query_effect_amount_model import PublicQueryAmountError
from ..upkeep_hand_difference_model import UPKEEP_HAND_DIFFERENCE_CAPABILITY,UPKEEP_HAND_DIFFERENCE_ORIGIN


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
    if (len(specs)!=1 or getattr(program,'event',None)!='step.begin' or getattr(program,'active_zone',None)!='battlefield'
            or getattr(program,'target_schema',False) is not None or getattr(program,'handlers',()) or len(program.effects)!=1
            or not is_upkeep_participant_schedule(program.event_condition)
            or len(program.event_condition['all'])!=2):
        raise ValueError('Hand difference requires its single nontargeted opponent-upkeep frame')
    effect=program.effects[0]
    if (not isinstance(effect,Mapping) or set(effect)!={'op','source','target','amount'}
            or effect['op']!='damage' or effect['source']!='$source' or effect['target']!='$context.player'
            or ScalarEffectAmountSpec.from_dict(effect['amount'])!=specs[0]):
        raise ValueError('Hand difference requires one sealed upkeep-player source-damage result')
    return (UPKEEP_HAND_DIFFERENCE_CAPABILITY,SCALAR_AMOUNT_CAPABILITY,SCHEDULED_PLAYER_CAPABILITY,
            'damage.amount.positive','damage.result.player_life')


def is_closed_upkeep_hand_difference_program(program):
    try:required=upkeep_hand_difference_program_dependencies(program)
    except (ValueError,TypeError,KeyError):return False
    return bool(required) and set(required)<=set(program.capability_dependencies)


def resolve_upkeep_hand_difference(host,item,difference):
    program=host.semantics.get(item.semantic_key)
    context=item.context;seat=context.get('player')
    if (program is None or not upkeep_hand_difference_program_dependencies(program)
            or item.kind!='triggered_ability' or item.targets or context.get('event')!='step.begin'
            or context.get('step')!='upkeep' or type(seat) is not str or seat==item.controller
            or seat not in host.state.players):
        raise PublicQueryAmountError('Hand difference lost its sealed upkeep participant')
    player=host.state.players[seat]
    if _difference_specs(program)[0].hand_difference!=difference:
        raise PublicQueryAmountError('Hand difference changed its sealed quantity descriptor')
    if not player.in_game:return 0
    if host.state.step!='upkeep' or host.state.active_player!=seat or not isinstance(player.zones.get('hand'),list):
        raise PublicQueryAmountError('Hand difference requires its current live upkeep participant')
    return difference.evaluate(len(player.zones['hand']))
