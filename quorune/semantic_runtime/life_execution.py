from __future__ import annotations
from .intents import LifeChangeIntent,PayLifeIntent,PayManaCostIntent
from .life_batch_intent import LifeLossBatchIntent

LIFE_INTENT_TYPES=(LifeChangeIntent,LifeLossBatchIntent,PayLifeIntent,PayManaCostIntent)


def execute_life_intent(sink,intent):
    """Delegate one typed life or resource payment to its canonical owner."""
    if isinstance(intent,LifeLossBatchIntent):return intent.actor,sink.apply_life_loss_batch_intent(intent)
    if isinstance(intent,LifeChangeIntent):return intent.player,sink.apply_life_change_intent(intent)
    if isinstance(intent,PayLifeIntent):return intent.player,sink.pay_life_intent(intent)
    if isinstance(intent,PayManaCostIntent):
        sink.pay_mana_cost_intent(intent)
        return intent.player,None
    raise TypeError('Unknown life or payment intent')
