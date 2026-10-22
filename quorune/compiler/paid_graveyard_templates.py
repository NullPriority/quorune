from __future__ import annotations

"""Complete intrinsic graveyard clauses with fixed canonical payment data."""

import re

from ..additional_cost_vocabulary import DISCARD_ONE_COST, SACRIFICE_ONE_COST, EXILE_ONE_FROM_GRAVEYARD_COST
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..rules.casting_additional_cost_groups import FixedLifePaymentAdditionalCost
from ..rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from ..rules.graveyard_cast_costs import PaidGraveyardCastSpec, PAID_GRAVEYARD_HANDLER
from .fixed_numbers import fixed_number


_COUNT = r'(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|[1-9][0-9]*)'
_CLAUSE = re.compile(r'You may cast this (?:card|creature) from your graveyard by (?P<price>.+?) '
    r'(?P<mode>rather than paying its mana cost|in addition to paying its other costs)\.', re.I)
_MANA = r'(?:\{(?:[0-9]+|[WUBRGC])\})+'


def _resource_price(text):
    discard = re.fullmatch(rf'discarding (?P<count>{_COUNT}) cards?', text, re.I)
    sacrifice = re.fullmatch(rf'sacrificing (?P<count>{_COUNT}) (?P<subject>creatures?|artifacts? or creatures?)', text, re.I)
    exile = re.fullmatch(r'exiling another creature card from your graveyard', text, re.I)
    if discard:
        count = 1 if discard['count'].casefold() in {'a', 'an'} else fixed_number(discard['count'])
        return FixedZoneChangeAdditionalCost(DISCARD_ONE_COST, 'discard_cards',
            ObjectQuerySpec(zones=('hand',), owner='$actor', known_to_actor=True), count=count)
    if sacrifice:
        count = 1 if sacrifice['count'].casefold() in {'a', 'an'} else fixed_number(sacrifice['count'])
        query = {'types_any': ('artifact', 'creature')} if ' or ' in sacrifice['subject'].casefold() else {'types_all': ('creature',)}
        return FixedZoneChangeAdditionalCost(SACRIFICE_ONE_COST, 'sacrifice_cards',
            ObjectQuerySpec(zones=('battlefield',), controller='$actor', known_to_actor=True, **query), count=count)
    if exile:
        return FixedZoneChangeAdditionalCost(EXILE_ONE_FROM_GRAVEYARD_COST, 'exile_cards',
            ObjectQuerySpec(zones=('graveyard',), owner='$actor', known_to_actor=True, types_all=('creature',)))
    return None


def paid_graveyard_spec(text):
    material = ' '.join(text.strip().split())
    if ' — ' in material:
        label, material = material.split(' — ', 1)
        # CR207.2c-d labels have no rules meaning. Provider keyword metadata
        # also includes flavor words, so it cannot classify the label.
        if (re.fullmatch(r"[A-Z][A-Za-z']*(?: [A-Z][A-Za-z']*)*", label) is None
                or label.casefold().startswith(('if ', 'when ', 'whenever ', 'as ', 'you ', 'this ', 'that '))):
            return None
    match = _CLAUSE.fullmatch(material)
    if match is None:
        return None
    price = match['price']; alternate = match['mode'].casefold().startswith('rather')
    mana = None; costs = []
    paid_mana = re.fullmatch(rf'paying (?P<mana>{_MANA})(?: and (?P<resource>.+))?', price, re.I)
    paid_life = re.fullmatch(rf'paying (?P<amount>{_COUNT}) life(?: and (?P<resource>.+))?', price, re.I)
    resource = None
    if paid_mana:
        if not alternate:
            return None
        mana = paid_mana['mana'].upper(); resource = paid_mana['resource']
    elif paid_life:
        amount = 1 if paid_life['amount'].casefold() in {'a', 'an'} else fixed_number(paid_life['amount'])
        costs.append(FixedLifePaymentAdditionalCost(amount)); resource = paid_life['resource']
    else:
        resource = price
    if resource is not None:
        try:
            cost = _resource_price(resource)
        except (ValueError, TypeError):
            return None
        if cost is None:
            return None
        costs.append(cost)
    try:
        return PaidGraveyardCastSpec(mana if mana is not None else '{0}' if alternate else None,
            tuple(FrozenMap(cost.to_descriptor()) for cost in costs))
    except (ValueError, TypeError):
        return None


def paid_graveyard_template(text):
    spec = paid_graveyard_spec(text)
    if spec is None:
        return None
    return 'self-graveyard-fixed-payment-v1', {'handler_id': PAID_GRAVEYARD_HANDLER,
        'schema_version': 1, 'event': 'cast.zone.permission', 'source_zone': 'graveyard',
        'payment': spec.to_dict(), REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True}, spec.capabilities()
