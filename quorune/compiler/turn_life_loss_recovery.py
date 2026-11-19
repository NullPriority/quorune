"""Complete printed gain clauses over actual life lost during the current turn."""
from . import regex_working_set as re
from ..scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin,SCALAR_AMOUNT_MECHANIC
from ..turn_life_loss_amount_model import AllPlayerTurnLifeLossSpec,TURN_LIFE_LOSS_TOTAL_MECHANIC


def turn_life_loss_recovery_template(text,*,compile_fixed):
    body=' '.join(text.strip().split()).replace('’',"'")
    body=re.sub(r'\s*\(Damage causes loss of life\.\)$','',body,flags=re.I)
    folded=body.casefold()
    if folded=="you gain life equal to the life you've lost this turn.":
        amount=ScalarEffectAmountSpec(ScalarAmountOrigin.HISTORY,history_fact='life_lost').to_dict()
        mechanic=SCALAR_AMOUNT_MECHANIC
    elif folded=='you gain life equal to the total life lost by all players this turn.':
        amount=AllPlayerTurnLifeLossSpec().to_dict();mechanic=TURN_LIFE_LOSS_TOTAL_MECHANIC
    else:return None
    fixed=compile_fixed('You gain 1 life.')
    if fixed[0] is None or len(fixed[1])!=1 or fixed[2] is not None:return None
    effect=fixed[1][0]
    if effect!={'op':'life','player':'$controller','delta':1}:return None
    return 'turn-life-loss-recovery-v1',({**effect,'delta':amount},),None,tuple(dict.fromkeys((mechanic,*fixed[3])))
