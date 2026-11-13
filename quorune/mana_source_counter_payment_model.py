"""Versioned domain contract for exact source-counter mana payment."""
from collections.abc import Mapping

MANA_SOURCE_COUNTER_PAYMENT_VERSION_FIELD='mana_source_counter_payment_version'


def mana_source_counter_payment_version(value):
    if value is None:return None
    if type(value) is not int or value!=1:raise ValueError('Unsupported mana source-counter payment version')
    return value


def read_mana_source_counter_payment_version(data: Mapping):
    if MANA_SOURCE_COUNTER_PAYMENT_VERSION_FIELD not in data:return None
    value=data[MANA_SOURCE_COUNTER_PAYMENT_VERSION_FIELD]
    if value is None:raise ValueError('Declared mana source-counter payment version cannot be null')
    return mana_source_counter_payment_version(value)


def mana_source_counter_payment_fields(value):
    version=mana_source_counter_payment_version(value)
    return {} if version is None else {MANA_SOURCE_COUNTER_PAYMENT_VERSION_FIELD:version}


def exact_mana_source_counter_payment(state):
    return mana_source_counter_payment_version(getattr(state,MANA_SOURCE_COUNTER_PAYMENT_VERSION_FIELD,None))==1
