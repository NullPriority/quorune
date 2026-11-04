from __future__ import annotations

"""Live resolving-trigger authority for a fixed Miracle alternative cost."""

import hashlib

from ..card_programs.admission import program_has_complete_card_program_admission
from ..errors import GameRuleError, StateInvariantError
from ..replacement.immutable import thaw_value
from ..selection.fixed_resolving_cast import fixed_resolving_cast_options
from ..semantic_runtime.intrinsic_draw_reveals import MiracleCastTriggerHandler, MiracleIntrinsicRevealHandler
from ..semantic_runtime.miracle_intent import MiracleCastChoiceIntent
from ..util import stable_json
from .miracle_model import FixedMiracleCost, MiracleModelError, MiracleRevealedHand, MIRACLE_CHOICE_OPERATION
from .miracle_triggers import revealed_hand_for_trigger


def miracle_alternative_cost(cost: FixedMiracleCost):
    return {'id':'miracle','kind':'alternate','label':f'Miracle {cost.cost_text}',
            'requirements':thaw_value(cost.mana_cost)}


def _fingerprint(options):
    return hashlib.sha256(stable_json([thaw_value(option) for option in options]).encode()).hexdigest()


def bind_miracle_cast_instruction(item, effect):
    if effect.get('op') != MIRACLE_CHOICE_OPERATION:
        return effect
    if set(effect) != {'op','cost'}:
        raise MiracleModelError('Miracle instruction has a closed schema')
    hand = revealed_hand_for_trigger(item)
    if hand is None or FixedMiracleCost.from_dict(effect['cost']) != hand.cost:
        raise MiracleModelError('Miracle instruction lost its linked reveal')
    return {**effect,'hand':hand.to_dict()}


def miracle_cast_options(host, *, item, actor, effect):
    if (item is None or item.kind != 'triggered_ability' or item.controller != actor
            or actor not in host.active_seats or not item.context.get('currently_resolving')):
        return ()
    hand = revealed_hand_for_trigger(item)
    if (hand is None or MiracleRevealedHand.from_dict(effect.get('hand')) != hand
            or FixedMiracleCost.from_dict(effect.get('cost')) != hand.cost
            or item.source_object_id != hand.card_object_id):
        raise MiracleModelError('Miracle choice differs from the live trigger binding')
    program = host.semantics.get(item.semantic_key)
    if (program is None or 'miracle_linked_cast_window' not in program.coverage
            or not host.semantic_program_is_current_trusted(program)
            or not program_has_complete_card_program_admission(program)
            or len(program.handlers) != 1
            or MiracleCastTriggerHandler().validate(program.handlers[0]) != hand.cost):
        return ()
    card = host.state.cards.get(hand.card_object_id)
    try:
        hand.require_current_hand(card)
    except MiracleModelError:
        return ()
    source_program = host.semantics.get(hand.program_key)
    if (source_program is None or source_program.oracle_id != card.oracle_id
            or program.oracle_id != card.oracle_id
            or not host.semantic_program_is_current_trusted(source_program)
            or not program_has_complete_card_program_admission(source_program)
            or len(source_program.handlers) != 1
            or MiracleIntrinsicRevealHandler().validate(source_program.handlers[0]) != hand.cost):
        return ()
    return fixed_resolving_cast_options(host,actor=actor,card=card,
                                       cost_option=miracle_alternative_cost(hand.cost),option_id='miracle')


def execute_miracle_cast_choice(host, intent: MiracleCastChoiceIntent):
    if not isinstance(intent, MiracleCastChoiceIntent):
        raise GameRuleError('Miracle cast choice intent is malformed')
    item = next((item for item in host.state.stack if item.ref == intent.source.stack_ref),None)
    if (item is None or item.controller != intent.actor or not item.context.get('currently_resolving')
            or revealed_hand_for_trigger(item) != intent.hand):
        raise GameRuleError('Miracle resolving trigger changed')
    options = miracle_cast_options(host,item=item,actor=intent.actor,
        effect={'cost':intent.hand.cost.to_dict(),'hand':intent.hand.to_dict()})
    if _fingerprint(options) != intent.options_fingerprint:
        raise GameRuleError('Miracle cast options changed')
    if intent.choice == 'decline':
        return
    if not options:
        raise GameRuleError('The Miracle card is not currently castable')
    response = dict(thaw_value(intent.response))
    face = response.get('face')
    selected = response.get('cost_option') or 'miracle'
    matches = [option for option in options if option['id'] == selected
               and (face is None or option.get('face') == face)]
    if len(matches) != 1:
        raise GameRuleError('Select one current Miracle casting option')
    card = host.state.cards[intent.hand.card_object_id]
    response.update({'card':card.ref,'from':'hand','cost_option':matches[0]['id'],'auto_pay':True})
    if matches[0].get('face') is not None:
        response['face'] = matches[0]['face']
    before = {stack.ref for stack in host.state.stack}
    host._cast(intent.actor,response,authorized_from_zone='hand',
               authorized_cost_option=miracle_alternative_cost(intent.hand.cost),
               ignore_priority=True,ignore_timing=True,during_resolution=True)
    if not any(stack.ref not in before and stack.kind == 'spell' and stack.card_object_id == card.object_id
               for stack in host.state.stack):
        raise StateInvariantError('The Miracle choice created no spell')
