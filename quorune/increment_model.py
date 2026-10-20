from __future__ import annotations

"""CR 702.191: immutable paid-mana/current-creature comparison contract."""

CAPABILITY='counter.producer.increment'
CONDITION_FIELD='increment_paid_mana_exceeds_source_size'


def increment_condition_holds(*,is_creature,power,toughness,mana_spent):
    if type(is_creature) is not bool or any(type(value) is not int for value in (power,toughness,mana_spent)) or mana_spent<0:
        raise ValueError('Increment requires typed current characteristics and nonnegative paid mana')
    return is_creature and (mana_spent>power or mana_spent>toughness)
