from __future__ import annotations

"""Resolution-time fixed-color mana through existing amount expressions."""

from ..characteristic_fragments import CharacteristicQuantitySpec
from ..live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
from ..query_effect_amount_model import PublicQueryAmountSpec, PublicQueryAmountError
from .public_quantity_mana import public_quantity_mana_template


def public_quantity_mana_effect_template(text, *, source_name):
    output = public_quantity_mana_template(text, source_name=source_name)
    if output is None or output.restriction is not None:
        return None
    if isinstance(output.amount, (CharacteristicQuantitySpec, LiveBattlefieldQuantitySpec)):
        try:
            amount = PublicQueryAmountSpec(output.amount).to_dict()
        except PublicQueryAmountError:
            return None
        marker = 'public-query-effect-amount'
    else:
        amount = output.amount.to_dict()
        marker = 'scalar-effect-amount'
    if output.selection!='fixed':
        from ..variable_resolution_mana_model import VariableResolutionManaChoiceSpec
        spec=VariableResolutionManaChoiceSpec('one_color' if output.selection=='choose_one' else 'combination',output.colors,amount)
        return ('public-quantity-resolution-mana-choice-v3',(spec.effect(),),None,('public-quantity-mana',marker,'variable-resolution-mana-choice'))
    return ('public-quantity-resolution-mana-v1',
        ({'op':'mana','player':'$controller','color':output.colors[0],'amount':amount,'source':'$source'},),
        None, ('public-quantity-mana', marker))
