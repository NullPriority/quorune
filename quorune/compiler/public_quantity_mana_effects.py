from __future__ import annotations

"""Resolution-time fixed-color mana through existing amount expressions."""

from . import regex_working_set as re
from ..characteristic_fragments import CharacteristicQuantitySpec
from ..live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
from ..query_effect_amount_model import PublicQueryAmountSpec, PublicQueryAmountError
from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC, TARGET_HAND_COUNT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec
from .life_templates import _player_target_schema
from .public_quantity_mana import public_quantity_mana_template


_TARGET_HAND_COUNT_MANA = re.compile(
    r"Add \{(?P<color>[WUBRGC])\} for each card in target opponent['\u2019]s hand\.", re.I,
)


def _target_hand_count_mana_template(text):
    match = _TARGET_HAND_COUNT_MANA.fullmatch(text.strip())
    if match is None:
        return None
    amount = ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET_HAND_COUNT, schema_version=4)
    effect = {
        "op": "mana", "player": "$controller", "color": match["color"].upper(),
        "amount": amount.to_dict(), "source": "$source",
    }
    return (
        "public-target-hand-count-mana-v1", (effect,), _player_target_schema("opponent"),
        ("public-quantity-mana", SCALAR_AMOUNT_MECHANIC, TARGET_HAND_COUNT_MECHANIC, "cr-115-targets"),
    )


def public_quantity_mana_effect_template(text, *, source_name):
    output = public_quantity_mana_template(text, source_name=source_name)
    if output is None:
        return _target_hand_count_mana_template(text)
    if output.restriction is not None:
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
