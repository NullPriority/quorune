from __future__ import annotations

"""Closed declaration for opponent loss followed by actual-total gain."""

ACTUAL_LIFE_TRANSFER_CAPABILITY='life.transfer.actual_opponent_loss'
ACTUAL_LIFE_TRANSFER_OPERATION='life_loss_then_gain_actual'
ACTUAL_LIFE_TRANSFER_MECHANIC='cr-119-life'


def actual_life_transfer_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or len(effects)!=1 or ACTUAL_LIFE_TRANSFER_MECHANIC not in mechanic_ids:return ()
    e=effects[0]
    if (set(e)!={'op','schema_version','player','amount'} or e['op']!=ACTUAL_LIFE_TRANSFER_OPERATION
            or type(e['schema_version']) is not int or e['schema_version']!=1 or e['player']!='$controller'
            or type(e['amount']) is not int or e['amount']<=0):return ()
    return ('life.change.effect',ACTUAL_LIFE_TRANSFER_CAPABILITY)
