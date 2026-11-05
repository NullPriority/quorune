from __future__ import annotations

"""Replay canonical cost choices inside one atomic rules-authorized exile cast."""

from ..errors import GameRuleError
from ..replacement.immutable import thaw_value
from ..replacement_effects import ReplacementChoiceRequired
from .random_discard_additional_cost import freeze_random_spell_cost_response


def commit_resolving_free_exile_cast(host,*,actor,response,payment_id,replacement_selections=()):
    if type(payment_id) is not str or not payment_id:
        raise GameRuleError('A resolving cast payment requires its server identity')
    if any(type(key) is not str or key.startswith('_') for key in response):
        raise GameRuleError('Internal casting-payment fields cannot be submitted')
    payload={**response,'_mana_payment_id':payment_id}
    payload=freeze_random_spell_cost_response(host,actor,payload)
    selections=tuple(replacement_selections);journal={}
    for index in range(len(selections)+1):
        try:
            with host.transaction():
                host._cast(actor,{**payload,'_mana_replacement_selections':journal},
                    authorized_from_zone='exile',force_without_mana_cost=True,
                    ignore_priority=True,ignore_timing=True,during_resolution=True)
                if index!=len(selections):
                    raise GameRuleError('The resolving cast contains unused cost replacement choices')
            return
        except ReplacementChoiceRequired as required:
            if index==len(selections):raise
            pending=required.pending
            if pending.event_order_options:
                raise GameRuleError('A resolving cast cost cannot change its committed event order')
            event=required.batch.events[pending.event_index]
            if event.kind not in {'zone.change','counter.place','damage'} or not event.event_id:
                raise GameRuleError('The resolving cast cost replacement event is unrepresented')
            journal.setdefault(event.event_id,[]).append(thaw_value(selections[index]))
