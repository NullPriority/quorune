"""Additive public departure-name facts with truthful unavailable legacy history."""
from collections.abc import Mapping

DEATH_RULES_NAMES_FIELD='departure_rules_names'


from .death_history_name_model import (validate_event_rules_names, decode_event_rules_names,
    validate_death_name_history, death_name_history_fields)


def capture_departure_rules_names(host,card,characteristics):
    history=getattr(host.state,'turn_history',None)
    if getattr(history,'death_name_history_version',None)!=1 or card.zone!='battlefield':
        return characteristics
    from .carddb_characteristics import public_object_rules_names
    names=tuple(sorted(set(public_object_rules_names(card,characteristics,host.card_record(card)))))
    validate_event_rules_names('permanent_died',names)
    return {**characteristics,DEATH_RULES_NAMES_FIELD:names}


def occurrence_death_name_fields(history,characteristics):
    if getattr(history,'death_name_history_version',None)!=1:return {}
    names=characteristics.get(DEATH_RULES_NAMES_FIELD)
    validate_event_rules_names('permanent_died',names)
    if names is None:raise ValueError('A new death occurrence lost its sealed public name facts')
    return {'rules_names':names}
