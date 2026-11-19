"""Closed event-bound countering and player game-loss instructions."""
from . import regex_working_set as re
from .event_player_results import _event_player


def fixed_event_outcome_template(binding, body, *, card_name, effect_template):
    del card_name, effect_template
    source = re.fullmatch(r'Counter it unless you sacrifice (?:a|an) (?P<type>artifact|battle|creature|enchantment|land|planeswalker)\.',body.strip(),re.I)
    if (source is not None and binding.event.value == 'spell.cast'
            and getattr(getattr(binding,'spell_subject',None),'source_spell',False)):
        from ..cast_source_sacrifice_payment import CastSourceSacrificePaymentSpec
        from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
        spec = CastSourceSacrificePaymentSpec(source['type'].casefold())
        return ('fixed-cast-source-sacrifice-or-counter-v1',(spec.effect(),),None,(FIXED_EFFECT_PAYMENT_MECHANIC,)),False
    if binding.event.value == 'spell.cast' and re.fullmatch(r'Counter that spell\.', body.strip(), re.I):
        return ('fixed-cast-event-counter-v1', ({'op':'counter_stack_target','stack':'$context.stack'},), None, ()), False
    if (re.fullmatch(r'That player loses the game\.', body.strip(), re.I)
            and _event_player(binding, 'that player') == '$context.target'):
        return ('fixed-damage-player-game-loss-v1', ({'op':'lose_game','player':'$context.target'},), None, ()), False
    return None
