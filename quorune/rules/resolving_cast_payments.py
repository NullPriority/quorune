from __future__ import annotations

"""Replay canonical cost choices inside one atomic rules-authorized cast."""

from ..errors import GameRuleError
from ..replacement.immutable import thaw_value
from ..replacement_effects import ReplacementChoiceRequired
from .random_discard_additional_cost import freeze_random_spell_cost_response


def resolving_price_requires_cast(option):
    """CR 118.8c exempts a required price with a stated hidden-card quality."""
    from ..object_predicate import ObjectQuerySpec
    from .casting_additional_costs import fixed_zone_change_additional_cost
    from .reveal_additional_cost import fixed_reveal_additional_cost

    unrestricted_hand = ObjectQuerySpec(zones=("hand",), owner="$actor", known_to_actor=True)
    for cost in option.get("additional_costs", ()):
        if fixed_reveal_additional_cost(cost) is not None:
            return False
        move = fixed_zone_change_additional_cost(cost)
        if (move is not None and move.origin_zone == "hand" and move.count is not None and move.count > 0
                and move.predicate != unrestricted_hand):
            return False
    return True


def commit_resolving_exile_cast(host, *, actor, response, payment_id, replacement_selections=(),
                               force_without_mana_cost=True):
    return commit_resolving_cast(
        host, actor=actor, response=response, payment_id=payment_id, origin="exile",
        replacement_selections=replacement_selections, force_without_mana_cost=force_without_mana_cost,
    )


def commit_resolving_cast(host, *, actor, response, payment_id, origin, replacement_selections=(),
                         force_without_mana_cost=True):
    if origin not in ("exile", "graveyard") or origin == "graveyard" and not force_without_mana_cost:
        raise GameRuleError("Resolving cast origin and price are unsupported")
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
                    authorized_from_zone=origin,force_without_mana_cost=force_without_mana_cost,
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
