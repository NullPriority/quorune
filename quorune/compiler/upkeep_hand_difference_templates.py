"""Whole-body scheduled damage and life effects using the participant’s public hand size."""
from copy import deepcopy
from . import regex_working_set as re
from ..entry_designations import LINKED_OPPONENT_UPKEEP_TEMPLATE, LINKED_OPPONENT_UPKEEP_VARIANT
from ..rules.source_references import SourceReferenceSpec
from ..rules.upkeep_hand_differences import CONTROLLER_HAND_SURPLUS, controller_upkeep_hand_surplus_condition
from ..upkeep_hand_difference_model import UpkeepHandDifferenceSpec,HandDifferenceDirection as Direction

_FORMS={
    'the number of cards in their hand minus 4':(Direction.HAND_MINUS_CONSTANT,4),
    '3 minus the number of cards in their hand':(Direction.CONSTANT_MINUS_HAND,3),
}


def controller_upkeep_hand_surplus_result(binding, body, *, effect_template, card_name):
    if (binding.event.value != "step.begin" or binding.active_zone != "battlefield"
            or binding.variant != "your upkeep" or body != binding.body
            or binding.event_condition != controller_upkeep_hand_surplus_condition()
            or re.fullmatch(r"You gain X life, where X is the number of cards in your hand minus 4[.]", body.strip(), re.I) is None):
        return None
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC
    from ..rules.scheduled_player_shapes import SCHEDULED_PLAYER_MECHANIC

    template, effects, schema, mechanics = effect_template("You gain 1 life.", card_name=card_name)
    if template is None or schema is not None or effects != ({"op": "life", "player": "$controller", "delta": 1},):
        return None
    effect = {**effects[0], "player": "$context.player", "delta": CONTROLLER_HAND_SURPLUS.scalar_amount()}
    return ("scheduled-player-hand-difference-v1", (effect,), None,
            (*mechanics, SCALAR_AMOUNT_MECHANIC, SCHEDULED_PLAYER_MECHANIC)), True


def upkeep_hand_difference_result(binding,body,*,effect_template,card_name):
    linked = binding.variant == LINKED_OPPONENT_UPKEEP_VARIANT
    if (binding.event.value != "step.begin" or binding.variant not in {"each_opponent_upkeep", LINKED_OPPONENT_UPKEEP_VARIANT}
            or binding.active_zone != "battlefield"):
        return None
    source=rf'(?:this artifact|this creature|{SourceReferenceSpec(card_name).regex_pattern})'
    match=re.fullmatch(rf'(?P<source>{source}) deals X damage to that player, where X is (?P<amount>.+)\.',body.strip(),re.I)
    if match is not None:
        form = " ".join(match["amount"].casefold().split())
        if form not in _FORMS:
            return None
        amount = UpkeepHandDifferenceSpec(*_FORMS[form])
        result_template = LINKED_OPPONENT_UPKEEP_TEMPLATE if linked else "scheduled-player-hand-difference-v1"
    elif linked:
        return None
    else:
        source = rf"(?:this enchantment|{SourceReferenceSpec(card_name).regex_pattern})"
        match = re.fullmatch(
            rf"(?P<source>{source}) deals damage to that player equal to the number of cards "
            r"in that player['\u2019]s hand\.",
            body.strip(), re.IGNORECASE,
        )
        if match is None:
            return None
        amount = UpkeepHandDifferenceSpec(Direction.HAND_MINUS_CONSTANT, 0, schema_version=2)
        result_template = "scheduled-player-current-hand-damage-v2"
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC
    from ..rules.scheduled_player_shapes import SCHEDULED_PLAYER_MECHANIC
    template,effects,schema,mechanics=effect_template(f"{match['source']} deals 1 damage to target player.",card_name=card_name)
    if (template is None or len(effects)!=1 or schema is None or schema.get('zones')!=['player']
            or schema.get('count')!=1 or effects[0].get('op')!='damage'
            or effects[0].get('target')!='$target.0' or effects[0].get('source')!='$source'):return None
    effect=deepcopy(dict(effects[0]));effect['target']='$context.player'
    effect['amount'] = amount.scalar_amount()
    return ((result_template,(effect,),None,
             tuple(dict.fromkeys((*(m for m in mechanics if m!='cr-115-targets'),
                                  SCALAR_AMOUNT_MECHANIC,SCHEDULED_PLAYER_MECHANIC)))),True)
